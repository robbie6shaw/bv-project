from typing import Sequence
from numpy.typing import ArrayLike
import pymatgen.core as pmg
import math, logging, sys
import numpy as np
import pandas as pd
import fileIO

class BVStructure:

    DB_LOCATION = "soft-bv-params.sqlite3"

    # --TESTED--
    def __init__(self, inputStr:str):
        """
            Initialises a BVStructure object using an input string generated by the fileIO module.
        """
        
        lines = inputStr.splitlines()

        self.rCutoff = 6
        self.conductor = (lines[0].split("\t")[0], int(lines[0].split("\t")[1]))
        self.params = tuple(map(float, lines[1].split("\t")))
        extraP = lines[2].split("\t")
        self.volume = float(extraP[0])
        self.vectors = np.zeros((3,3))
        for i in range(3,6):
            cols = lines[i].split("\t")
            for j in range(3):
                self.vectors[i-3][j] = float(cols[j])

        sites = []
        for i in range(7, len(lines)):
            data = lines[i].split("\t")
            sites.append({"label": data[0], "element": data[1], "ox_state": round(float(data[2])), "lp": bool(data[3]), "coords": np.array((float(data[4]), float(data[5]), float(data[6])))})

        self.sites = pd.DataFrame(sites)
        self.getBVParams()

    # --TESTED--
    def from_file(fileName:str):
        """
            Initialises a BVStructure object from an input file
        """

        if fileName[-4:] != ".inp":
            logging.error(f"Incorrect file format. The input file should be an inp file and not a {fileName[-3:]} file")
            sys.exit()
    
        with open(fileName, "r") as f:
            contents = f.read()
            if len(contents) > 5: return BVStructure(contents)     
            else: raise Exception("Empty input file")

    # --TESTED--   
    def translateCoord(self, coord:np.ndarray, shift:tuple) -> np.ndarray:
        """
            Translates a coordinate using the vectors provided with the structure. The shift can be both integers or floats. Returns a numpy array.
        """
        return coord + self.vectors[0]*shift[0] + self.vectors[1]*shift[1] + self.vectors[2]*shift[2]
    
    # --TESTED--
    def insideSpace(self, start:np.ndarray, end:np.ndarray, point:np.ndarray) -> bool:
        """
            Checks whether a coordinate is inside a space bounded by two points. All arguments must be numpy arrays.
        """
        return (start <= point).all() and (point <= end).all()
    
    # --TESTED--
    def getBVParams(self):
        """
            Method that fetches bond valence parameters for all species in the structure and the conducting ion. This method populates the bvParams dictionary and sets a cutoff radius using the values stored in the database.
        """

        self.bvParams = {}
        maxCutoff = 0

        db = fileIO.BVDatabase(self.DB_LOCATION)
        # For every ion that is not the conductor (currently assuming only one )
        # for i, fixedIon in self.sites.drop_duplicates(subset=["element","ox_state"]).iterrows():
        for i, fixedIon in self.sites.drop_duplicates(subset=["label"]).iterrows():
            if fixedIon["ox_state"] * self.conductor[1] > 0:
                continue
            else:
                self.bvParams[fixedIon["label"]] = db.getParams(self.conductor, (fixedIon["element"], fixedIon["ox_state"]))
                maxCutoff = max(maxCutoff, self.bvParams[fixedIon["label"]][3])

        self.rCutoff = maxCutoff

    def defineBufferArea(self):
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
        self.coreCartesian = np.sum(self.findCoreCell(self.bufferArea) * self.vectors, axis=0)

        # Find the actual volume made by the cutoff radius and the core cell, allowing any other sites to be disregarded
        self.reqVolStart = self.coreCartesian - np.array((self.rCutoff,self.rCutoff,self.rCutoff))
        self.reqVolEnd = self.coreCartesian + np.sum(self.vectors, axis=0) + np.array((self.rCutoff,self.rCutoff,self.rCutoff))

    def findBufferedSites(self):
        """
            Using the buffer area generated in defineBufferArea(), this creates a list of sites within the correct bounds
        """

        # Create a copy of the sites dataframe to add to
        self.bufferedSites = pd.DataFrame(columns=["label","element","ox_state","lp","coords"])

        # For every cell in the determine buffer area
        for h in range(self.bufferArea[0]):
            for k in range(self.bufferArea[1]):
                for l in range(self.bufferArea[2]):
                    
                    # Skip if the cell is already there
                    if h == 0 and k == 0 and l == 0: continue

                    # For every site in the core cell
                    for i, site in self.sites.iterrows():

                        # Find its new site in the translated cell
                        newCoord = self.translateCoord(site["coords"], (h,k,l))
                        
                        # If the site is outwith the required area, disregard it
                        if self.insideSpace(self.reqVolStart, self.reqVolEnd, newCoord):
                            self.bufferedSites.loc[len(self.bufferedSites)] = [site["label"], site["element"], site["ox_state"], site["lp"], newCoord]

    def setUpVoxels(self):
        """
            Setup the map array to store data for each voxel. Requires a resolution to have been set in the structure.
        """
        # Calculate the number of voxels in each axis that is required to achieve the requested resolution
        self.voxelNumbers = np.zeros(3, dtype=int)

        for i in range(3):
            self.voxelNumbers[i] = math.ceil(self.params[i] / self.resolution)

        # Initalise a map of dimensions that match the number of voxels
        self.map = np.zeros(self.voxelNumbers)

    def calcCartesian(self, shift:np.ndarray):
        """
            Calculates the cartesian coordinates of voxel in the map using the origin of the 'core cell' and an integer shift. \n

            Returns a numpy array of floats defining the voxels cartesian coordinates.
        """
        # position = np.copy(self.coreCartesian)
        
        return self.coreCartesian + np.sum((shift / self.voxelNumbers).reshape(3,1) * self.vectors, axis=0)
        # for i in range(3):
        #     position[i] += shift[i] * self.resolution

        # return position
    
    def calcDistanceWCutoff(self, point1, point2):
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
    
    def sign(num):
        return -1 if num < 0 else 1

    def initaliseMap(self, resolution:int):
        """
            Initialises a map for storing the calculated BVS values. Creates a buffer cell structure, finds the core cells coordinates within that strcuture and defines the number of voxels. Arguments: \n
            resolution - Set a resolution for the map in armstrongs.
        """

        # Define the resolution
        self.resolution = resolution
        
        self.defineBufferArea()
        self.findBufferedSites()
        self.setUpVoxels()
        
        logging.info("Successful initalisation of the map")

    def populateMap(self):
        """
            Main function that populates the map space with BVS values. 
        """

        # Removes all conducting ions from the structure
        # selectedAtoms = self.bufferedSites[self.bufferedSites["element"] != self.conductor[0]]
        selectedAtoms = self.bufferedSites[self.bufferedSites["ox_state"] * self.conductor[1] < 0]
        print(selectedAtoms)

        # For every voxel
        for h in range(self.voxelNumbers[0]):
            for k in range(self.voxelNumbers[1]):
                for l in range(self.voxelNumbers[2]):

                    # Calculate the voxels cartesian coordinates
                    pos = self.calcCartesian(np.array((h,k,l)))

                    # Initialise the bond valence sum
                    bvSum = 0.

                    # For each atom in the structure
                    for i, fixedIon in selectedAtoms.iterrows():

                        # Calculate the point to point distance between the voxel position and the atom position
                        ri = self.calcDistanceWCutoff(pos, fixedIon["coords"])

                        # If the seperation is less than 1 Å, set the BV value to very high value so the site is disregarded. This will cause the atom loop to be exited -> The site has a BV too high to be considered.
                        if ri < 1:
                            bvSum = 100
                            break

                        # If the seperation is greater than the cutoff radius, the bv contribution is 0.
                        elif ri > self.rCutoff:
                            continue

                        # Otherwise, calcualted the BV value and add it to the total
                        else:
                            r0, ib = self.bvParams[fixedIon["label"]][0:2]
                            bv = calcBV(r0, ri, ib)
                            bvSum += bv

                    # Update the map
                    self.map[h][k][l] = bvSum

            logging.info(f"Completed plane {h} out of {self.voxelNumbers[0] - 1}")

    def deltaBV(self, value:float, ion:str):
        if ion == "F-" or ion == "Na+":
            result = abs(value - 1)
            return result

    def exportMap(self, fileName:str, dataType:str):

        if dataType == "delta":
            deltaBV_vector = np.vectorize(self.deltaBV, excluded=("ion"))
            export = deltaBV_vector(self.map, "F-")
        else:
            export = self.map

        print(export)

        with open(fileName, 'w') as file:
            file.write("%s\n" % ("TEMP NAME - PBSNF4"))
            file.write("%f %f %f %f %f %f\n" % self.params)
            file.write("%i %i %i\n" % tuple(self.voxelNumbers.tolist()))

            export.tofile(file,"  ")

    def createLonePairs(self, distance:int = 1):
        pass



def generate_structure(cifFile:str) -> pmg.Structure:
    return pmg.Structure.from_file(cifFile)

def calcBV(r0:float, ri:float, ib:float) -> float :
    """
        Calculate the bond valence from distance. Arguments: \n
        r0 - The radius bond valence parameter \n
        ri - The current distance \n
        ib - The inverse of the bond valence parameter \n

        Will be implemented in fortran to improve performance
    """
    return math.exp((r0 - ri) * ib)

def calcPPDistance(point1, point2):
    deltaX = point2[0] - point1[0]
    deltaY = point2[1] - point1[1]
    deltaZ = point2[2] - point1[2]

    return math.sqrt(deltaX**2 + deltaY**2 + deltaZ**2)

def calcPSDistance(x:float, y:float, z:float, site:pmg.PeriodicSite) -> float:
    """
        Calculate the distance between a point and a site. Arguments: \n
        x,y,z - Cartesian coordinates of the point \n
        site - A Pymatgen Object representing the site
    """
    return site.distance_from_point((x,y,z))

def calcSSDistance(site1:pmg.PeriodicSite, site2:pmg.PeriodicSite) -> float:
    """
        Calculates the distance between two sites. Does not use the pmg.PeriodicSite.distance as it does strange things with periodicity.
    """
    return calcPSDistance(site2.x, site2.y, site2.z, site1)

def findSiteBVS(site:pmg.PeriodicSite, structure:pmg.Structure, radius:int = 6 ) -> float:
    """
        Calculates the bond valence sum for a particular site located in a structure. The stucture must be ordered. Arguments: \n
        site - The site to calculate the bvs from. \n
        structure - The structure that the site is located in. \n
        radius - The cutoff radius for the bvs calculation. Defaults to 6 Å. \n
    """

    bvParams = {'Sn2+':(1.925, 2.702), 'Pb2+':(2.03, 2.702)}
    
    # Create temporary copy of structure
    tempStruct = structure.copy()

    # Ensure the site chosen only has one element on it
    if len(site.species.elements) != 1:
        raise Exception("Site chosen for BVS sum is disordered - must choose site that is ordered")

    # Find the species within the defined radius
    coordAtoms = structure.get_neighbors(site, radius)

    # Set bond valence sum to 0
    bvs = 0.

    for atom in coordAtoms:
        
        # If the oxidation state is the either both positive or both negative, disregard it from the bond valence sum
        # bool1 = atom.specie.oxi_state > 0
        # bool2 = site.specie.oxi_state > 0
        # bool3 = bool1 ^ bool2
        if ((atom.specie.oxi_state > 0) ^ (site.specie.oxi_state > 0)):

            # print(atom, end=' : ')
            
            # Find the bond valence parameters
            r0, ib = bvParams[atom.species_string]

            # Add this contribution to the bond valence
            bv = calcBV(r0, calcSSDistance(site, atom), ib)
            # print(calcSSDistance(site, atom), end=", ")
            # print(bv)
            bvs += bv
        
    return bvs

def bvsCif (fileLocation:str):
    """
        Find bond valence sum for all fluoride sites in a structure.
    """
    structure = generate_structure(fileLocation)

    for site in structure.sites:
        if site.species_string == "F-":
            print(f"F- Site at ({site.x}, {site.y}, {site.z}): {findSiteBVS(site, structure)}")

# bvsCif("cif-files/Binary Fluorides/ICSD_CollCode5270 (beta-PbF2).cif")
# pbsnf4 = BVStructure.from_file("pbsnf4.inp")
# print(pbsnf4.sites)
# pbsnf4.initaliseMap(1)
# pbsnf4.populateMap()
# pbsnf4.exportMap("result.grd", "delta")