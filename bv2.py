import math, logging, sys, os
import numpy as np
import pandas as pd
from fileIO import *
from pathlib import Path
import scipy.special as ss
from line_profiler import profile
from numba import njit, float64
from numba.core import types
from numba.typed import Dict

class BVStructure:

    DB_LOCATION = "soft-bv-params.sqlite3"
    LONE_PAIR_STRENGTH_CUTOFF = 0.5 # The magnitude of the bond valence vector required for the program to decide that a lone pair dummy site is required
    BOHR_IN_ANGSTROM = 0.5291772 # Conversion factor between bohr radius and angstroms for generating cube files
    CHARGE_CONVERSION = 0.594445 # Converts charge for cube files
    SCREENING_FACTOR = 0.75 # Factor for the ERFC
    LONE_PAIR_RADIUS = 1
    LONE_PAIR_CHARGE = -2

    CORE = core()

    # --TESTED--
    def __init__(self, inputStr:str, name:str, bvse:bool=False):
        """
            Initialises a BVStructure object using an input string generated by the fileIO module.
            Optional parameter to create dictionary for all BV parameters, in preperation for doing
            vector sums later in the program. Defaults to false.
        """
        
        lines = inputStr.splitlines()

        # Set starting cutoff radius, should be altered later in code
        self.rCutoff = 6

        # Extract header data from the input file
        self.conductor = Ion(element = lines[0].split("\t")[0], ox_state = int(lines[0].split("\t")[1]))
        self.params = tuple(map(float, lines[1].split("\t")))
        extraParams = lines[2].split("\t")
        self.volume = float(extraParams[0])
        self.vectors = np.zeros((3,3))
        self.name = name
        
        for i in range(3,6):
            cols = lines[i].split("\t")
            for j in range(3):
                self.vectors[i-3][j] = float(cols[j])

        self.inverseVectors = np.linalg.inv(self.vectors)

        # Setup the sites dataframe
        self.sites = pd.DataFrame(columns=["label","ion","ox_state","lp","coords"])
    
        # Fill the sites dataframe
        for i in range(7, len(lines)):
            data = lines[i].split("\t")
            self.sites.loc[data[1]] = [data[0], Ion(data[2], round(float(data[3]))), round(float(data[3])), bool(int(data[4])), np.array((float(data[5]), float(data[6]), float(data[7])))]

        logging.debug(self.sites)

        # Get the needed bond valence parameters from the database
        self.db = BVDatabase(self.DB_LOCATION)
        self.bvParams = self.get_param_dict(self.conductor, bvse)

        # Find the effective charges of ions in the structure
        self.chargeList = self.find_effective_charges()
        

    # --TESTED--
    @classmethod
    def from_file(cls, path:str|Path, bvse = False):
        """
            Initialises a BVStructure object from an input file
        """

        if isinstance(path, str):
            path = Path(path)

        if path.suffix != ".inp":
            logging.error(f"Incorrect file format. The input file should be an inp file and not a {path.suffix} file")
            sys.exit()

        try:
            with open(path, "r") as f:
                contents = f.read()
                if len(contents) > 5: 
                    return BVStructure(contents, name = path.stem, bvse = bvse)     
                else: 
                    logging.fatal(f"The file at {path} was malformed. Quitting.")
                    sys.exit()
        except FileNotFoundError:
            logging.fatal(f"The file at {path} could not be found. Quitting.")
            sys.exit()

    # --TESTED--   
    def translate_coord(self, coord:np.ndarray, shift:tuple|np.ndarray) -> np.ndarray:
        """
            Translates a coordinate using the vectors provided with the structure. The shift can be both integers or floats. Returns a numpy array.
        """
        if not isinstance(shift, np.ndarray):
            shift = np.array(shift)
        return coord + np.matmul(shift, self.vectors)
        # return coord + self.vectors[0]*shift[0] + self.vectors[1]*shift[1] + self.vectors[2]*shift[2]
    
    # --TESTED--
    def _frac_from_cart(self, cartesian:np.ndarray):
        """
            Translates a cartesian coordinate in angstroms to a fractional coordinate using matrix multiplication. 
        """
        return np.matmul(cartesian, self.inverseVectors)
    
    # --TESTED--
    def _cart_from_frac(self, fractional:np.ndarray):
        """
            Translates a fractional coordinate to a cartesian coordinate in angstroms using matrix multiplication. 
        """
        return np.matmul(fractional, self.vectors)
    
    # --TESTED--
    def inside_space(self, start:np.ndarray, end:np.ndarray, point:np.ndarray) -> bool:
        """
            Checks whether a coordinate is inside a space bounded by two points. All arguments must be *fractional coordinates* and numpy arrays.
        """
        return (start <= point).all() and (point <= end).all()
    
    # --TESTED--
    def get_param_dict(self, conductor:Ion, bvse = False):
        """
            Method that fetches bond valence parameters for all species in the structure and the specified ion. This method populates the bvParams dictionary and sets a cutoff radius using the values stored in the database.
        """

        bvParams = {}
        maxCutoff = 0

        # For every ion that is not the conductor (currently assuming only one)
        for ion in self.sites.drop_duplicates(subset=["label"])["ion"]:

            if ion == conductor:
                continue

            dictKey = f"{conductor}.{ion}"

            if dictKey in bvParams:
                continue

            else:
                params = self.db.get_bv_params(conductor, ion, bvse=bvse)
                if params is None:
                    continue
                elif params.r_cutoff is not None:
                    maxCutoff = max(maxCutoff, params.r_cutoff)

                bvParams[f"{conductor}.{ion}"] = params

        self.rCutoff = maxCutoff
        return bvParams

    def get_bv_param(self, ion1:Ion, ion2:Ion):
        try:
            return self.bvParams[f"{ion1}.{ion2}"]
        except KeyError:
            try:
                return self.bvParams[f"{ion2}.{ion1}"]
            except KeyError:
                self.bvParams[f"{ion1}.{ion2}"] = self.db.get_bv_params(ion1, ion2, bvse=True)
                return self.bvParams[f"{ion1}.{ion2}"]
        
    def conductor_bv_param(self, ion:Ion):
        return self.get_bv_param(self.conductor, ion)

    def define_buffer_area(self):
        """
            Defines all the attributes of the buffer area, to get the program ready for creating the list of sites in the buffer region.
            Only intended to be used within the initaliseMap() method. 
        """

        # The default starting point for the buffer area is a 3x3x3 supercell.
        self.bufferArea = np.array((3,3,3))
        
        # If the current buffer area does not enclose the the volume made by the cutoff radius and the core cell, add more cells to ensure coverage.
        for i in range(3):
            if self.params[i] < self.rCutoff:
                self.bufferArea[i] += 2

        # Find the cartesian coordinates of the 'core' cell - the one that map will be based on
        # To do this, it find the core cell coordinates in terms of cells and then multiplies the cell vectors
        self.findCoreCell = np.vectorize(lambda x: math.floor(x/2))
        # self.coreCartesian = np.sum(self.findCoreCell(self.bufferArea) * self.vectors, axis=0)

        # Find the actual volume made by the cutoff radius and the core cell, allowing any other sites to be disregarded
        self.reqVolStart = - np.array((self.rCutoff,self.rCutoff,self.rCutoff))
        self.reqFracStart = self._frac_from_cart(self.reqVolStart)
        self.reqVolEnd =  np.sum(self.vectors, axis=0) + np.array((self.rCutoff,self.rCutoff,self.rCutoff))
        self.reqFracEnd = self._frac_from_cart(self.reqVolEnd)

    def find_buffer_sites(self):
        """
            Using the buffer area generated in defineBufferArea(), this creates a list of sites within the correct bounds
        """

        # Create a copy of the sites dataframe to add to
        self.bufferedSites = pd.DataFrame(columns=["label","ion","ox_state","lp","coords"])

        # For every site in the core cell
        for site in self.sites.itertuples():

            # For every cell that needs to be expanded to
            # Range if buffer area is 3, creates area from -1 -> 1; if 5, -2 -> 2 
            # Note - range function does not include last number ∴ must have ceiling function for upper limit
            for h in range(- math.floor(self.bufferArea[0]/2), math.ceil(self.bufferArea[0]/2)):
                for k in range(- math.floor(self.bufferArea[1]/2), math.ceil(self.bufferArea[1]/2)):
                    for l in range(- math.floor(self.bufferArea[2]/2), math.ceil(self.bufferArea[2]/2)):

                        # Find its new site in the translated cell
                        newCoord = self.translate_coord(site.coords, (h,k,l))
                        
                        # If the site is outwith the required area, disregard it
                        if self.inside_space(self.reqFracStart, self.reqFracEnd, self._frac_from_cart(newCoord)):
                            self.bufferedSites.loc[f"{site.Index}({h}{k}{l})"] = [site.label, site.ion, site.ox_state, site.lp, newCoord]

        logging.debug("Buffered sites have been generated:")
        logging.debug(self.bufferedSites)

    def setup_voxels(self, resolution:float):
        """
            Setup the map array to store data for each voxel. Requires a resolution to have been set in the structure.
        """
        # Calculate the number of voxels in each axis that is required to achieve the requested resolution
        self.voxelNumbers = np.zeros(3, dtype=int)

        for i in range(3):
            minimumVoxel = math.ceil(self.params[i] / resolution)
            self.voxelNumbers[i] = math.floor(minimumVoxel/12 + 1)* 12

        # Initalise a map of dimensions that match the number of voxels
        self.map = np.zeros(self.voxelNumbers)

    def calc_voxel_cartesian(self, shift:np.ndarray):
        """
            Calculates the cartesian coordinates of voxel in the map using the origin of the 'core cell' and an integer shift. \n

            Returns a numpy array of floats defining the voxels cartesian coordinates.
        """
              
        return np.sum((shift / self.voxelNumbers).reshape(3,1) * self.vectors, axis=0)
    
    def calc_distance(self, point1, point2):
        """
            Calculates the distance between two points. If the the distance on one axis exceeds the cutoff distance, only that axes distance is returned.
        """

        deltaX = abs(point2[0] - point1[0])
        if deltaX > self.rCutoff:  return deltaX
        deltaY = abs(point2[1] - point1[1])
        if deltaY > self.rCutoff:  return deltaY
        deltaZ = abs(point2[2] - point1[2])
        if deltaZ > self.rCutoff:  return deltaZ

        return math.sqrt(deltaX**2 + deltaY**2 + deltaZ**2)
    
    def calc_vector_distance(self, vector:np.ndarray):
        """
            Calculates the distance that a vector covers. If the distance on one axis exceeds the cutoff distance, only that axes distance is returned.
        """

        if vector.max() > self.rCutoff:
            return vector.max()
        else:
            return math.sqrt(np.dot(vector, vector))

    def initalise_map(self, resolution:int):
        """
            Initialises a map for storing the calculated BVS values. Creates a buffer cell structure, finds the core cells coordinates within that strcuture and defines the number of voxels. Arguments: \n
            resolution - Set a resolution for the map in armstrongs.
        """
        
        self.define_buffer_area()
        self.find_buffer_sites()
        self.setup_voxels(resolution)
        
        logging.info("Successful initalisation of the map")
        logging.debug(self.bufferedSites)

    def _linear_penalty(self, charge:int, distance:float, penaltyK:float):
        return penaltyK * (self.conductor.ox_state * charge)*(1/distance - 1/self.rCutoff)
    
    def _quadratic_penalty(self, charge:int, distance:float, penaltyK:float):
        return penaltyK * (self.conductor.ox_state * charge)*(1/distance**2 - 1/(self.rCutoff**2))

    def populate_map_bvsm(self, penalty:float = 0, fType:str = "linear", only_penalty:bool = False):
        """
            Function that populates the map with the bond valence mismatch values. A penalty function can be enabled with the parameter of `penalty`. If the value is 0, no penalty is added; otherwise this is the constant of proportionaltity is used in the penalty function. Recommended values are around 0.1.
        """

        if fType in ["linear", "lin", "l", "1"]:
            penF = self._linear_penalty
        elif fType in ["quadratic", "quad", "q", "2"]:
            penF = self._quadratic_penalty

        # Removes all conducting ions from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["ion"] != self.conductor]

        # For every voxel
        for h in range(self.voxelNumbers[0]):
            for k in range(self.voxelNumbers[1]):
                for l in range(self.voxelNumbers[2]):

                    # Calculate the voxels cartesian coordinates
                    pos = self.calc_voxel_cartesian(np.array((h,k,l)))

                    # Initialise the bond valence sum
                    bvSum = 0.
                    penaltySum = 0.

                    # For each atom in the structure
                    for i, ionSite in selectedAtoms.iterrows():

                        # Calculate the point to point distance between the voxel position and the atom position
                        ri = self.calc_distance(pos, ionSite["coords"])


                        # If the seperation is greater than the cutoff radius, the bv contribution is 0.
                        if ri > self.rCutoff:
                            continue

                        elif (ionSite["ion"].ox_state * self.conductor.ox_state) < 0:
                            if not only_penalty:
                                # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
                                if ri < 1:
                                    bvSum = 20
                                    break

                                # Otherwise, calculated the BV value and add it to the total
                                else:
                                    bvParam = self.conductor_bv_param(ionSite["ion"])
                                    bvSum += calc_bv(bvParam.r0, ri, bvParam.ib)

                        elif penalty != 0 :
                            penaltySum += penF(-2, ri, penalty)

                    # Update the map
                    if only_penalty:
                        self.map[h][k][l] = penaltySum
                    else:
                        self.map[h][k][l] = abs(bvSum - abs(self.conductor.ox_state)) + penaltySum

            logging.info(f"Completed plane {h} out of {self.voxelNumbers[0] - 1}")

    @profile
    def populate_map_bvse(self, mode = 1):
        """
            --- DEPRECATED ---
            Populates the map with BVSE data. Mode Settings:
                0 - Only Bonding Energy
                1 - Bonding + Coulombic Energy
                2 - Only Coulombic Energy
        """

        # Removes all conducting ions from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["ion"] != self.conductor]

        conductorRadius = self.db.get_radius(self.conductor)

        # For every voxel
        for h in range(self.voxelNumbers[0]):
            for k in range(self.voxelNumbers[1]):
                for l in range(self.voxelNumbers[2]):

                    # Calculate the voxels cartesian coordinates
                    pos = self.calc_voxel_cartesian(np.array((h,k,l)))

                    # Initialise the bond valence sum
                    Ebond = 0.
                    Ecoul = 0.

                    # For each atom in the structure
                    for i, ionSite in selectedAtoms.iterrows():

                        # Calculate the point to point distance between the voxel position and the atom position
                        ri = self.calc_distance(pos, ionSite["coords"])


                        # If the seperation is greater than the cutoff radius, the bv contribution is 0.
                        if ri > self.rCutoff:
                            continue

                        elif (ionSite["ox_state"] * self.conductor.ox_state) < 0:
                        
                            if mode < 2:

                                # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
                                if ri < 1:
                                    Ebond = 20
                                    break

                                # Otherwise, calculated the BV value and add it to the total
                                else:
                                    params = self.conductor_bv_param(ionSite["ion"])
                                    Ebond += calc_Ebond(params.d0, params.rmin, ri, params.ib)

                        elif mode > 0:

                            if ionSite["label"][:2] == "lp":
                                Ecoul += calc_Ecoul(self.conductor.ox_state, -2, ri, conductorRadius, self.LONE_PAIR_RADIUS, self.SCREENING_FACTOR)
                            else:
                                params = self.bvParams[self.conductor_bv_param(ionSite["ion"])]
                                Ecoul += calc_Ecoul(self.conductor.ox_state, ionSite["ion"].ox_state, ri, params.i1r, params.i2r, self.SCREENING_FACTOR) 

                        # elif penalty != 0 :
                        #     penaltySum += penF(-2, ri, penalty)

                    self.map[h][k][l] = Ebond + Ecoul
                    # Update the map
                    # if only_penalty:
                    #     self.map[h][k][l] = penaltySum
                    # else:
                    #     self.map[h][k][l] = abs(Ebond - abs(self.conductor)) + penaltySum

            logging.info(f"Completed plane {h} out of {self.voxelNumbers[0] - 1}")

    @profile
    def populate_map_bvsm_jit(self, mode = 1, penalty:float = 0.05):
        """
            Populates the map with BVSE data. Mode Settings:
                0 - Normal BVSM
                1 - BVSM + Penalty
                2 - Only Penalty Function
        """

        # Removes all conducting ions from the structure
        selectedSites = self.bufferedSites[self.bufferedSites["ion"] != self.conductor]

        bvIons = self._create_bv_array(selectedSites)
        penIons = self._create_bv_penalty_array(selectedSites, penalty)

        self.map = bvsm_map(self.voxelNumbers, self.vectors, self.rCutoff, self.conductor.ox_state, mode, bvIons, penIons, self.map)
        logging.info(f"Succesful map creation for {self.name}")

    def _create_bv_array(self, selectedSites):
        selectedSites = selectedSites[(selectedSites["ox_state"] * self.conductor.ox_state) < 0]
        outList = []
        for i, site in selectedSites.iterrows():
            siteInfo = np.zeros(5)
            siteInfo[0:3] = site["coords"].copy()
            params = self.conductor_bv_param(site["ion"])
            siteInfo[3] = params.ib
            siteInfo[4] = params.r0
            outList.append(siteInfo)
        out = np.array(outList)
        if out.shape[0] == 0:
            out = np.array([[]])
        return out
    
    def _create_bv_penalty_array(self, selectedSites, penalty):
        selectedSites = selectedSites[(selectedSites["ox_state"] * self.conductor.ox_state) > 0]
        outList = []
        for i, site in selectedSites.iterrows():
            siteInfo = np.zeros(5)
            siteInfo[0:3] = site["coords"].copy() 
            siteInfo[3] = self.LONE_PAIR_CHARGE
            siteInfo[4] = penalty
            outList.append(siteInfo)
        out = np.array(outList)
        if out.shape[0] == 0:
            out = np.array([[]])
        return out

    @profile
    def populate_map_bvse_jit(self, mode = 1, effectiveCharge = True):
        """
            Populates the map with BVSE data. Mode Settings:
                0 - Only Bonding Energy
                1 - Bonding + Coulombic Energy
                2 - Only Coulombic Energy
        """

        # Removes all conducting ions from the structure
        selectedSites = self.bufferedSites[self.bufferedSites["ion"] != self.conductor]

        bondIons = self._create_bond_site_array(selectedSites)
        coulIons = self._create_coul_site_array(selectedSites, effectiveCharge)

        self.map = bvse_map(self.voxelNumbers, self.vectors, self.rCutoff, mode, self.SCREENING_FACTOR, bondIons, coulIons, self.map)
        logging.info(f"Succesful map creation for {self.name}")

    def _create_bond_site_array(self, selectedSites):
        selectedSites = selectedSites[(selectedSites["ox_state"] * self.conductor.ox_state) < 0]
        outList = []
        for i, site in selectedSites.iterrows():
            siteInfo = np.zeros(6)
            siteInfo[0:3] = site["coords"].copy()
            params = self.conductor_bv_param(site["ion"])
            siteInfo[3] = params.d0
            siteInfo[4] = params.rmin
            siteInfo[5] = params.ib
            outList.append(siteInfo)
        out = np.array(outList)
        if out.shape[0] == 0:
            out = np.array([[]])
        return out
    
    def _create_coul_site_array(self, selectedSites, effectiveCharge):
        selectedSites = selectedSites[(selectedSites["ox_state"] * self.conductor.ox_state) > 0]
        conductorRadius = self.db.get_radius(self.conductor)
        outList = []
        for i, site in selectedSites.iterrows():
            siteInfo = np.zeros(7)
            siteInfo[0:3] = site["coords"].copy()
            if site["ion"].element != "LP":
                params = self.conductor_bv_param(site["ion"])
                if effectiveCharge:
                    siteInfo[3] = self.chargeList[self.conductor]
                    siteInfo[4] = self.chargeList[site.ion]
                else:
                    siteInfo[3] = site["ion"].ox_state
                    siteInfo[4] = self.conductor.ox_state
                siteInfo[5] = params.i1r
                siteInfo[6] = params.i2r
            else:
                if effectiveCharge:
                    siteInfo[3] = self.chargeList[self.conductor]
                else:   
                    siteInfo[3] = self.conductor.ox_state 
                siteInfo[4] = self.LONE_PAIR_CHARGE
                siteInfo[5] = conductorRadius
                siteInfo[6] = self.LONE_PAIR_RADIUS
            outList.append(siteInfo)
        out = np.array(outList)
        if out.shape[0] == 0:
            out = np.array([[]])
        return out

    def _delta_bv(self, value:float, ion:str):
        if ion == "F-" or ion == "Na+":
            result = abs(value - 1)
            return result
        
    def export_map(self, path:Path|str):
        """
            Exports the produced map to a file. The file name should end with the supported formats - either .grd or .cube.
        """

        if isinstance(path, str):
            path = Path(path)

        if path.is_file():
            for i in range(100):
                trialPath = path.with_stem(f"{path.stem}-{i}")
                if not trialPath.is_file():
                    path = trialPath
                    break
            
            logging.info(f"File already exists - writing to file {path} instead")

        if path.suffix == ".grd":
            self._export_grd(path)
        elif path.suffix == ".cube":
            self._export_cube(path)
        else:
            logging.error("Unsupported export file type (.grd or .cube supported), exporting a temp.grd file instead in home directory")
            self._export_grd("temp.grd")

    def _export_grd(self, path:Path):
        """
            Exports the map to a grd file.
        """

        with open(path, 'w') as file:

            for ion in self.sites.groupby("ion", sort=False).size().items():
                file.write(ion.__str__())
            file.write("    Conducting:%s\n" % (self.conductor.element))

            file.write("%f %f %f %f %f %f\n" % self.params)
            file.write("%i %i %i\n" % tuple(self.voxelNumbers.tolist()))

            self.map.tofile(file,"  ")

    def _export_cube(self, path:Path):
        """
            Exports the map to a cube file.
        """
        
        with open(path, "w") as file:
            
            total = 0
            elementDict = {}

            for ion in self.sites.groupby("ion", sort=False).size().items():
                file.write(ion[0].__str__())
                total += ion[1]
                elementDict[ion[0].element] = self.db.get_atomic_no(ion[0].element)
            file.write("\nConducting = %s ; sf = 0.750000;\n" % (self.conductor.__str__()))

            file.write("%i  0.000000   0.000000   0.000000\n" % (total))

            for i in range(3):
                voxelVector = self.vectors[i]/(self.voxelNumbers[i] * self.BOHR_IN_ANGSTROM)
                file.write("%i  %7.6f   %7.6f   %7.6f\n" % ((self.voxelNumbers[i],) + tuple(voxelVector)))

            for site in self.sites.itertuples():
                file.write("%i %7.6f    %7.6f   %7.6f   %7.6f\n" % ((elementDict[site.ion.element], self.chargeList[site.ion]) + tuple(site.coords/self.BOHR_IN_ANGSTROM)))
            
            self.map.tofile(file, "\n")
            

    def reset_map(self):
        """
            Resets the map back to its blank state, filled with zeroes.
        """
        self.map = np.zeros(self.voxelNumbers)

    # Can't use pycifrw, as starfile code has errors 
    def export_cif(self, outFile:str):

        with open(outFile, 'w') as f:
            f.write("bv-project-export\n")

            cellParams = ["_cell_length_a",
                          "_cell_length_b",
                          "_cell_length_c",
                          "_cell_angle_alpha",
                          "_cell_angle_beta",
                          "_cell_angle_gamma"
                          ]
            
            for i in range(len(cellParams)):
                f.write(f"{cellParams[i]} {self.params[i]}\n")

            f.write("_space_group_IT_number 1\n")
            f.write("loop_\n_atom_site_label\n_atom_site_type_symbol\n_atom_site_fract_x\n_atom_site_fract_y\n_atom_site_fract_z\n_atom_site_occupancy\n")
            lpSwap = lambda x: "He" if x == "LP" else x
            for site in self.bufferedSites.itertuples():
                fracCoords = self._frac_from_cart(site.coords)
                if self.inside_space(np.zeros(3), np.array((1,1,1)), fracCoords):
                    f.write(f"{site.label} {lpSwap(site.ion.element)} {fracCoords[0]} {fracCoords[1]} {fracCoords[2]} 1\n")


    def find_site_bvs(self, p1Label:str, vector=False) -> np.ndarray:
        """
            Finds the bond valence sum for a particular site in the lattice. Arguments: \n
            p1Label - The P1 label of the site in the input file -  this uniquely identifies the site within one unit cell. \n
            vector - If true, calculates the vector bond valence sum; if false simply calculates the normal sum. \n
            Returns a numpy array that is the vector bond valence sum.
        """

        if vector:
            bvsFunction = calc_vector_bv
            vbvSum = np.zeros(3)
        else:
            bvsFunction = calc_bv
            vbvSum = 0.

        logging.debug(f"Calculating site BVS for {p1Label}")
        # Get series representing the ion in question
        targetSite = self.sites.loc[p1Label]
        # Removes all anions/cations from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["ox_state"] * targetSite["ion"].ox_state < 0]

        # For each atom in the structure
        for fixedSite in selectedAtoms.itertuples():

            vector = targetSite["coords"] - fixedSite.coords

            # Calculate the point to point distance between the voxel position and the atom position
            ri = self.calc_vector_distance(vector)

            # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
            if ri < 1:
                vbvSum = 100
                break

            # If the seperation is greater than the cutoff radius, the bv contribution is 0.
            elif ri > self.rCutoff:
                continue

            # Otherwise, calcualted the BV value and add it to the total
            else:
                params = self.get_bv_param(fixedSite.ion, targetSite["ion"])
                vbv = bvsFunction(params.r0, ri, params.ib, vector)
                vbvSum += vbv

        return vbvSum

    def create_lone_pairs(self, distance:int = 1):
        """
            Method that creates dummy sites representing lone pairs in the structure. Accepts optional argument to set the distance these lone pairs should be from the atom that they reside on.
        """

        # Create a dictionary of the lone pair sites and the unit vector representing their direction.
        lpSiteDict = {}

        lonePairIon = Ion("LP", -2)
        
        for site in self.sites[self.sites["lp"]].itertuples():
            vbvs = self.find_site_bvs(site.Index, vector=True)
            magVbvs = np.linalg.norm(vbvs)
            if magVbvs > self.LONE_PAIR_STRENGTH_CUTOFF:
                lpNormVec = vbvs / magVbvs
                lpSiteDict[site.Index] = lpNormVec

        # For each of these sites in the buffered array, add a lone pair dummy site.
        for site in self.bufferedSites[self.bufferedSites["lp"]].itertuples():
            p1Label = site.Index.split("(")[0]
            if p1Label in lpSiteDict.keys():
                self.bufferedSites.loc["lp" + site.Index] = [f"lp{site.label}", lonePairIon, -2, 0, site.coords + lpSiteDict[p1Label]*distance]

        logging.debug(self.bufferedSites)

    def find_effective_charges(self):

        chargeDf = pd.DataFrame(columns=["V","n","N"])

        for ion, N in self.sites.groupby("ion", sort=False).size().items():
            chargeDf.loc[ion] = [ion.ox_state, self.db.get_period(ion), N]

        chargeDf["part"] = chargeDf["V"] * chargeDf["N"] / np.sqrt(chargeDf["n"])

        anionSum = chargeDf[chargeDf["V"] < 0]["part"].sum()
        cationSum = chargeDf[chargeDf["V"] > 0]["part"].sum()
        out = {}

        for ion in chargeDf.itertuples():
            if ion.V < 0:
                out[ion.Index] = ion.V / math.sqrt(ion.n) * math.sqrt(abs(cationSum/anionSum))
            elif ion.V > 0:
                out[ion.Index] = ion.V / math.sqrt(ion.n) * math.sqrt(abs(anionSum/cationSum))
            
        return out


# ----- JITED FUNCTIONS -----

@njit(cache=True)
def calc_bv(r0:float, ri:float, ib:float, vector = None) -> float :
    """
        Calculate the bond valence from distance. Arguments: \n
        r0 - The radius bond valence parameter \n
        ri - The current distance \n
        ib - The inverse of the bond valence parameter \n
    """
    return math.exp((r0 - ri) * ib)

@njit(cache=True)
def calc_penalty(ri:float, q1:int, q2:int, penaltyK:float, rCutoff:float) -> float:
    return penaltyK * (q1 * q2)*(1/(ri**2) - 1/(rCutoff**2))

@njit(cache=True)
def calc_vector_bv(r0: float, ri:float, ib:float, vector:np.ndarray) -> np.ndarray:
    """
        Calculates the bond valence vector. Arguments:\n
        r0 - The radius bond valence parameter \n
        ri - The current distance \n
        ib - The inverse of the bond valence parameter \n
        vector - The vector between the two atoms \n
        Returns a vector representing the bond valence.
    """
    return calc_bv(r0, ri, ib) * vector / ri

@njit(float64(float64, float64, float64, float64),cache=True)
def calc_Ebond(d0:float, rmin:float, ri:float, ib:float) -> float:
    """
        Calculates the bonding energy for BVSE.
    """
    return d0 * (np.exp((rmin - ri)*ib) -1 )**2 - d0

@njit(float64(float64, float64, float64, float64, float64, float64), cache=True)
def calc_Ecoul(q1:float, q2:float, ri:float, r1:float, r2:float, f:float) -> float:
    """
        Calculates the Columbic repulsion energy for BVSE.
    """
    return (q1* q2)/ri * math.erfc(ri/(f*(r1 + r2)))

@njit(float64(float64[:], float64[:], float64), cache=True)
def calc_distance(point1, point2, cutoff=1000.) -> float:
    """
        Calculates the distance between two points, with a cutoff value.
    """
    
    deltaX = abs(point2[0] - point1[0])
    if deltaX > cutoff:  return deltaX
    deltaY = abs(point2[1] - point1[1])
    if deltaY > cutoff:  return deltaY
    deltaZ = abs(point2[2] - point1[2])
    if deltaZ > cutoff:  return deltaZ

    return math.sqrt(deltaX**2 + deltaY**2 + deltaZ**2)

@njit(locals=dict(r=float64), cache=True)
def voxel_bvse(voxelId:np.ndarray, voxelNos:np.ndarray, vectors:np.ndarray, cutoff:float, mode:int, screeningFactor:float, bondIons:np.ndarray, coulIons:np.ndarray):
    """
        Function to calculate the BVSE at a specific point. Uses numba to do Just-In-Time compliation for the function, for
        peformance improvements. Arguments: \n
        Position - A 3 element numpy array indicating the voxel position in angstroms. \n
        Cutoff - The radius cutoff for the energy function. \n
        Mode - An integer indicating what parts of the BVSE calculation to complete. \n
        Screening Factor - The screening factor for the Coulumbic repulsion calculation. \n
        Bond Ions - A numpy array indicating all ions to calculate the bond energy with. Has format of
        [[x, y, z, d0, rmin, ib]] \n
        Coul Ions - A numpy array indicating all ions to calculate the Coulumbic repulsion with. Has format of
        [[x, y, z, q1, q2, r1, r2, screeningFactor]]
    """
    
    position = np.sum((voxelId/voxelNos).reshape(3,1) * vectors, axis=0)
    Ebond = 0.
    Ecoul = 0.
    r = 0.

    if mode < 2:
        for i in range(bondIons.shape[0]):
            
            ion = bondIons[i]

            r = calc_distance(position, ion[:3], cutoff*2)

            if r > cutoff:
                continue

            # elif r < 1:
            #     Ebond = 20.
            #     break
            
            else:
                Ebond += calc_Ebond(d0=ion[3], rmin=ion[4], ri=r, ib=ion[5])

    if mode > 0:
        for i in range(coulIons.shape[0]):

            ion = coulIons[i]
            
            r = calc_distance(position, ion[:3], cutoff*2)

            if r > cutoff:
                continue
            else:
                Ecoul += calc_Ecoul(q1=ion[3], q2=ion[4], ri=r, r1=ion[5], r2=ion[6], f=screeningFactor)

    return Ebond + Ecoul

@njit(cache=True)
def bvse_map(voxelNos:np.ndarray, vectors:np.ndarray, cutoff:float, mode:int, screeningFactor:float, bondIons:np.ndarray, coulIons:np.ndarray, resultMap:np.ndarray):

    # For every voxel
    for h in range(voxelNos[0]):
        for k in range(voxelNos[1]):
            for l in range(voxelNos[2]):
                resultMap[h][k][l] = voxel_bvse(np.array((h, k, l)), voxelNos, vectors, cutoff, mode, screeningFactor, bondIons, coulIons)

    return resultMap

@njit(locals=dict(r=float64), cache=True)
def voxel_bvsm(voxelId:np.ndarray, voxelNos:np.ndarray, vectors:np.ndarray, cutoff:float, conductorOs:int, mode:int, bvIons:np.ndarray , penIons:np.ndarray):
    """
        Function to calculate the BVSM at a specific point. Uses numba to do Just-In-Time compliation for the function, for
        peformance improvements. Arguments: \n
        voxelId - A 3 element numpy array indicating the voxel position in angstroms. \n
        voxelNos - A 3 element numpy array indicating the number of voxels in the structure \n
        vectors - A 3 element numpy array of the lattice vectors
        cutoff - The radius cutoff for the energy function. \n
        conductorOS - The oxidation state of the conducting ion \n
        mode - An integer indicating what parts of the BVSE calculation to complete. \n
        bvIons - A numpy array indicating all ions to include in the bond valence sum. Has format of
        [[x, y, z, r0, ib]] \n
        penIons - A numpy array indicating all ions to calculate the penalty function with. Has format of
        [[x, y, z, q2, penaltyK]]
    """
    
    position = np.sum((voxelId/voxelNos).reshape(3,1) * vectors, axis=0)
    bvs = 0.
    penaltySum = 0.
    r = 0.

    if mode < 2:
        for i in range(bvIons.shape[0]):
            
            ion = bvIons[i]

            r = calc_distance(position, ion[:3], cutoff)

            if r > cutoff:
                continue
            
            else:
                bvs += calc_bv(r0=ion[4], ri=r, ib=ion[3])
    else:
        bvs = abs(conductorOs)        
    
    if mode > 0:
        for i in range(penIons.shape[0]):

            ion = penIons[i]
            
            r = calc_distance(position, ion[:3], cutoff)

            if r > cutoff:
                continue
            else:
                penaltySum += calc_penalty(ri=r, q1=conductorOs, q2=ion[3], penaltyK=ion[4], rCutoff=cutoff)

    return abs(bvs - abs(conductorOs)) + penaltySum

@njit(cache=True)
def bvsm_map(voxelNos:np.ndarray, vectors:np.ndarray, cutoff:float,  conductorOs:int, mode:int, bvIons:np.ndarray, penIons:np.ndarray, resultMap:np.ndarray):

    # For every voxel
    for h in range(voxelNos[0]):
        for k in range(voxelNos[1]):
            for l in range(voxelNos[2]):
                resultMap[h][k][l] = voxel_bvsm(np.array((h, k, l)), voxelNos, vectors, cutoff, conductorOs, mode, bvIons, penIons)

    return resultMap

