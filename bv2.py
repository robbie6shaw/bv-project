import math, logging, sys
import numpy as np
import pandas as pd
from fileIO import *
from scipy.special import erfc

class BVStructure:

    DB_LOCATION = "soft-bv-params.sqlite3"
    LONE_PAIR_STRENGTH_CUTOFF = 0.5 # The magnitude of the bond valence vector required for the program to decide that a lone pair dummy site is required
    BOHR_IN_ANGSTROM = 0.5291772 # Conversion factor between bohr radius and angstroms for generating cube files
    CHARGE_CONVERSION = 0.594445 # Converts charge for cube files
    CORE = core()

    # --TESTED--
    def __init__(self, inputStr:str, allParams=False, bvse=False):
        """
            Initialises a BVStructure object using an input string generated by the fileIO module.
            Optional parameter to create dictionary for all BV parameters, in preperation for doing
            vector sums later in the program. Defaults to false.
        """
        
        lines = inputStr.splitlines()

        # Set starting cutoff radius, should be altered later in code
        self.rCutoff = 6

        # Extract header data from the input file
        self.conductor = self.CORE.ion(element = lines[0].split("\t")[0], os = int(lines[0].split("\t")[1]))
        self.params = tuple(map(float, lines[1].split("\t")))
        extraParams = lines[2].split("\t")
        self.volume = float(extraParams[0])
        self.vectors = np.zeros((3,3))
        
        for i in range(3,6):
            cols = lines[i].split("\t")
            for j in range(3):
                self.vectors[i-3][j] = float(cols[j])

        self.inverseVectors = np.linalg.inv(self.vectors)

        # Setup the sites dataframe
        self.sites = pd.DataFrame(columns=["label","element","ox_state","lp","coords"])

        # Fill the sites dataframe
        for i in range(7, len(lines)):
            data = lines[i].split("\t")
            self.sites.loc[data[1]] = [data[0], data[2], round(float(data[3])), bool(int(data[4])), np.array((float(data[5]), float(data[6]), float(data[7])))]

        logging.debug(self.sites)

        # Get the needed bond valence parameters from the database
        self.db = BVDatabase(self.DB_LOCATION)
        self.allBvParams = self.get_bv_params(self.conductor, bvse)
        
        if allParams:
            self.allBvParams[self.conductor] = self.allBvParams 
        

    # --TESTED--
    def from_file(fileName:str, bvse = False):
        """
            Initialises a BVStructure object from an input file
        """

        if fileName[-4:] != ".inp":
            logging.error(f"Incorrect file format. The input file should be an inp file and not a {fileName[-3:]} file")
            sys.exit()

        try:
            with open(fileName, "r") as f:
                contents = f.read()
                if len(contents) > 5: 
                    return BVStructure(contents, bvse = bvse)     
                else: 
                    logging.fatal(f"The file at {fileName} was malformed. Quitting.")
                    sys.exit()
        except FileNotFoundError:
            logging.fatal(f"The file at {fileName} could not be found. Quitting.")
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
    def get_bv_params(self, inputIon, bvse = False):
        """
            Method that fetches bond valence parameters for all species in the structure and the specified ion. This method populates the bvParams dictionary and sets a cutoff radius using the values stored in the database.
        """

        bvParams = {}
        maxCutoff = 0

        # For every ion that is not the conductor (currently assuming only one)
        for p1Label, fixedIon in self.sites.drop_duplicates(subset=["label"]).iterrows():
            if fixedIon["ox_state"] * inputIon[1] > 0:
                continue
            else:
                bvParams[fixedIon["label"]] = self.db.getParams(inputIon, self.CORE.ion(element = fixedIon["element"], os = fixedIon["ox_state"]), bvse=bvse)
                maxCutoff = max(maxCutoff, bvParams[fixedIon["label"]].r_cutoff)

        self.rCutoff = maxCutoff
        return bvParams

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
        self.bufferedSites = pd.DataFrame(columns=["label","element","ox_state","lp","coords"])

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
                            self.bufferedSites.loc[f"{site.Index}({h}{k}{l})"] = [site.label, site.element, site.ox_state, site.lp, newCoord]

        logging.debug("Buffered sites have been generated:")
        logging.debug(self.bufferedSites)

    def setup_voxels(self):
        """
            Setup the map array to store data for each voxel. Requires a resolution to have been set in the structure.
        """
        # Calculate the number of voxels in each axis that is required to achieve the requested resolution
        self.voxelNumbers = np.zeros(3, dtype=int)

        for i in range(3):
            self.voxelNumbers[i] = math.ceil(self.params[i] / self.resolution)

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

        return math.sqrt(np.power(deltaX,2) + deltaY**2 + deltaZ**2)
    
    def calc_vector_distance(self, vector:np.ndarray):
        """
            Calculates the distance that a vector covers. If the distance on one axis exceeds the cutoff distance, only that axes distance is returned.
        """

        if vector.max() > self.rCutoff:
            return vector.max()
        else:
            return math.sqrt(np.dot(vector, vector))
    
    def sign(num):
        return -1 if num < 0 else 1

    def initalise_map(self, resolution:int):
        """
            Initialises a map for storing the calculated BVS values. Creates a buffer cell structure, finds the core cells coordinates within that strcuture and defines the number of voxels. Arguments: \n
            resolution - Set a resolution for the map in armstrongs.
        """

        # Define the resolution
        self.resolution = resolution
        
        self.define_buffer_area()
        self.find_buffer_sites()
        self.setup_voxels()
        
        logging.info("Successful initalisation of the map")
        logging.debug(self.bufferedSites)

    def populate_map_bvs(self):
        """
            Main function that populates the map space with BVS values. 
        """

        # Removes all anions/cations from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["ox_state"] * self.conductor.os < 0]

        # For every voxel
        for h in range(self.voxelNumbers[0]):
            for k in range(self.voxelNumbers[1]):
                for l in range(self.voxelNumbers[2]):

                    # Calculate the voxels cartesian coordinates
                    pos = self.calc_voxel_cartesian(np.array((h,k,l)))

                    # Initialise the bond valence sum
                    bvSum = 0.

                    # For each atom in the structure
                    for i, fixedIon in selectedAtoms.iterrows():

                        # Calculate the point to point distance between the voxel position and the atom position
                        ri = self.calc_distance(pos, fixedIon["coords"])

                        # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
                        if ri < 1:
                            bvSum = 20
                            break

                        # If the seperation is greater than the cutoff radius, the bv contribution is 0.
                        elif ri > self.rCutoff:
                            continue

                        # Otherwise, calcualted the BV value and add it to the total
                        else:
                            params = self.allBvParams[fixedIon["label"]]
                            bv = calc_bv(params.r0, ri, params.ib)
                            bvSum += bv

                    # Update the map
                    self.map[h][k][l] = bvSum

            logging.info(f"Completed plane {h} out of {self.voxelNumbers[0] - 1}")

    def _linear_penalty(self, charge:int, distance:float, penaltyK:float):
        return penaltyK * (self.conductor.os * charge)*(1/distance - 1/self.rCutoff)
    
    def _quadratic_penalty(self, charge:int, distance:float, penaltyK:float):
        return penaltyK * (self.conductor.os * charge)*(1/distance**2 - 1/(self.rCutoff**2))

    def populate_map_bvsm(self, penalty:float = 0, fType:str = "linear", only_penalty:bool = False):
        """
            Function that populates the map with the bond valence mismatch values. A penalty function can be enabled with the parameter of `penalty`. If the value is 0, no penalty is added; otherwise this is the constant of proportionaltity is used in the penalty function. Recommended values are around 0.1.
        """

        if fType in ["linear", "lin", "l", "1"]:
            penF = self._linear_penalty
        elif fType in ["quadratic", "quad", "q", "2"]:
            penF = self._quadratic_penalty

        # Removes all conducting ions from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["element"] != self.conductor.element]

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
                    for i, fixedIon in selectedAtoms.iterrows():

                        # Calculate the point to point distance between the voxel position and the atom position
                        ri = self.calc_distance(pos, fixedIon["coords"])


                        # If the seperation is greater than the cutoff radius, the bv contribution is 0.
                        if ri > self.rCutoff:
                            continue

                        elif (fixedIon["ox_state"] * self.conductor.os) < 0:
                            if not only_penalty:
                                # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
                                if ri < 1:
                                    bvSum = 20
                                    break

                                # Otherwise, calculated the BV value and add it to the total
                                else:
                                    r0, ib = self.allBvParams[fixedIon["label"]][0:2]
                                    bvSum += calc_bv(r0, ri, ib)

                        elif penalty != 0 :
                            penaltySum += penF(-2, ri, penalty)

                    # Update the map
                    if only_penalty:
                        self.map[h][k][l] = penaltySum
                    else:
                        self.map[h][k][l] = abs(bvSum - abs(self.conductor.os)) + penaltySum

            logging.info(f"Completed plane {h} out of {self.voxelNumbers[0] - 1}")

    def populate_map_bvse(self):

        # Removes all conducting ions from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["element"] != self.conductor.element]

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
                    for i, fixedIon in selectedAtoms.iterrows():

                        # Calculate the point to point distance between the voxel position and the atom position
                        ri = self.calc_distance(pos, fixedIon["coords"])


                        # If the seperation is greater than the cutoff radius, the bv contribution is 0.
                        if ri > self.rCutoff:
                            continue

                        elif (fixedIon["ox_state"] * self.conductor.os) < 0:
                        
                            # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
                            if ri < 1:
                                Ebond = 20
                                break

                            # Otherwise, calculated the BV value and add it to the total
                            else:
                                params = self.allBvParams[fixedIon["label"]]
                                Ebond += calc_Ebond(params.d0, params.rmin, ri, params.ib)
                                Ecoul += calc_Ecoul(self.conductor.os, fixedIon["ox_state"], ri, params.i1r, params.i2r, 0.75)

                        # elif penalty != 0 :
                        #     penaltySum += penF(-2, ri, penalty)

                    self.map[h][k][l] = Ebond + Ecoul
                    # Update the map
                    # if only_penalty:
                    #     self.map[h][k][l] = penaltySum
                    # else:
                    #     self.map[h][k][l] = abs(Ebond - abs(self.conductor)) + penaltySum

            logging.info(f"Completed plane {h} out of {self.voxelNumbers[0] - 1}")

    def _delta_bv(self, value:float, ion:str):
        if ion == "F-" or ion == "Na+":
            result = abs(value - 1)
            return result
        
    def export_map(self, fileName:str):
        """
            Exports the produced map to a file. The file name should end with the supported formats - either .grd or .cube.
        """
        if fileName[-3:] == "grd":
            self._export_grd(fileName)
        elif fileName[-4:] == "cube":
            self._export_cube(fileName)
        else:
            logging.error("Unsupported export file type (.grd or .cube supported), exporting a temp.grd file instead in home directory")
            self._export_grd("temp.grd")

    def _export_grd(self, fileName:str):
        """
            Exports the map to a grd file.
        """

        with open(fileName, 'w') as file:

            for atom in self.sites.groupby("element").size().items():
                file.write("%s:%i" % (self.CORE.ion_to_str(atom[0])))
            file.write("    Conducting:%s\n" % (self.conductor.element))

            file.write("%f %f %f %f %f %f\n" % self.params)
            file.write("%i %i %i\n" % tuple(self.voxelNumbers.tolist()))

            self.map.tofile(file,"  ")

    def _export_cube(self, fileName:str):
        """
            Exports the map to a cube file.
        """
        
        with open(fileName, "w") as file:
            
            total = 0
            elementDict = {}

            for atom in self.sites.groupby(["element","ox_state"]).size().items():
                file.write("%s:%i " % (self.CORE.ion_to_str(atom[0]), atom[1]))
                total += atom[1]
                elementDict[atom[0][0]] = self.db.get_atomic_no(atom[0][0])
            file.write("\nConducting = %s ; sf = 0.750000;\n" % (self.CORE.ion_to_str(self.conductor)))

            file.write("%i  0.000000   0.000000   0.000000\n" % (total))

            for i in range(3):
                voxelVector = self.vectors[i]/(self.voxelNumbers[i] * self.BOHR_IN_ANGSTROM)
                file.write("%i  %7.6f   %7.6f   %7.6f\n" % ((self.voxelNumbers[i],) + tuple(voxelVector)))

            for site in self.sites.itertuples():
                file.write("%i %7.6f    %7.6f   %7.6f   %7.6f\n" % ((elementDict[site.element], site.ox_state * self.CHARGE_CONVERSION) + tuple(site.coords/self.BOHR_IN_ANGSTROM)))
            
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
                    f.write(f"{site.label} {lpSwap(site.element)} {fracCoords[0]} {fracCoords[1]} {fracCoords[2]} 1\n")


            # for site in self.sites.itertuples():
            #     f.write(f"{site.label} {weeLambda(site.element)} {site.coords[0]/self.params[0]} {site.coords[1]/self.params[1]} {site.coords[2]/self.params[2]} 1\n")


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
        targetIon = self.sites.loc[p1Label]
        targetIonTuple = (targetIon["element"], int(targetIon["ox_state"]))
        # Removes all anions/cations from the structure
        selectedAtoms = self.bufferedSites[self.bufferedSites["ox_state"] * targetIon["ox_state"] < 0]

        # Ensure the required bv parmeters have been found
        if targetIonTuple not in self.allBvParams.keys():
            self.allBvParams[targetIonTuple] = self.get_bv_params(targetIonTuple)

        # For each atom in the structure
        for fixedIon in selectedAtoms.itertuples():

            vector = targetIon["coords"] - fixedIon.coords

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
                params = self.allBvParams[targetIonTuple][fixedIon.label]
                vbv = bvsFunction(params.r0, ri, params.ib, vector)
                vbvSum += vbv

        return vbvSum

    def create_lone_pairs(self, distance:int = 1):
        """
            Method that creates dummy sites representing lone pairs in the structure. Accepts optional argument to set the distance these lone pairs should be from the atom that they reside on.
        """

        # Create a dictionary of the lone pair sites and the unit vector representing their direction.
        lpSiteDict = {}
        
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
                self.bufferedSites.loc["lp" + site.Index] = [f"lp{site.label}", "LP", -2, 0, site.coords + lpSiteDict[p1Label]*distance]

        logging.debug(self.bufferedSites)

def calc_bv(r0:float, ri:float, ib:float, vector = None) -> float :
    """
        Calculate the bond valence from distance. Arguments: \n
        r0 - The radius bond valence parameter \n
        ri - The current distance \n
        ib - The inverse of the bond valence parameter \n

        Will be implemented in fortran to improve performance
    """
    return math.exp((r0 - ri) * ib)


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

def calc_Ebond(d0:float, rmin:float, ri:float, ib:float):
    return d0 * (np.exp((rmin - ri)*ib) -1 )**2 - d0

def calc_Ecoul(q1, q2, ri:float, r1:float, r2:float, f:float):
    return (q1 * q2)/ri * erfc(ri/(f*(r1 + r2)))

def calc_distance(point1, point2):
    deltaX = point2[0] - point1[0]
    deltaY = point2[1] - point1[1]
    deltaZ = point2[2] - point1[2]

    return math.sqrt(deltaX**2 + deltaY**2 + deltaZ**2)