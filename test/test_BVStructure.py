import unittest
import numpy as np
import pandas as pd
import calculate
import logging, math
from datetime import datetime

class alteredTestCase(unittest.TestCase):

    logging.basicConfig(
            level=logging.DEBUG, format='\n%(asctime)s -  %(levelname)s -  %(message)s', handlers=[
            # logging.FileHandler(f"./output/{datetime.now().isoformat(timespec='seconds')}.log"),
            logging.StreamHandler()
        ])

    def assertArrayAlmostEqual(self, array1:np.ndarray, array2):
        self.assertEqual(array1.size, array2.size)
        for i in range(len(array1)):
            self.assertAlmostEqual(array1[i], array2[i])

    def assertWithinBounds(self, num1:float, num2:float, bounds:float):
        self.assertGreater(num1, num2 - bounds)
        self.assertLess(num1, num2 + bounds)
        

class TestSimpleBVStructure(alteredTestCase):

    def setUp(self):
        self.obj = calculate.BVStructure.from_file("test/betaPbF2-simplified.inp")

    def test_conductor(self):
        self.assertEqual(self.obj.conductor[0], "F")
        self.assertEqual(self.obj.conductor[1], -1)

    def test_params(self):
        b = self.obj.params[1]
        self.assertTrue(isinstance(b,float))
        self.assertAlmostEqual(b, 5.9306)

    def test_volume(self):
        self.assertAlmostEqual(self.obj.volume, 208.591160224616)

    def test_vectors(self):
        self.assertIsInstance(self.obj.vectors, np.ndarray)
        self.assertAlmostEqual(self.obj.vectors[1][1], 5.9306)

    def test_sites(self):
        self.assertIsInstance(self.obj.sites, pd.DataFrame)
        self.assertEqual(len(self.obj.sites), 4)
        self.assertIsInstance(self.obj.sites["coords"][0], np.ndarray)
        self.assertAlmostEqual(self.obj.sites["coords"][1][1], 2.9653)
        self.assertTrue(self.obj.sites.loc["Pb1-0"]["lp"])
        self.assertFalse(self.obj.sites.loc["F1-0"]["lp"])

    def test_get_bv_params(self):
        self.assertIsInstance(self.obj.bvParams, dict)
        self.assertEqual(len(self.obj.bvParams), 1)
        self.assertEqual(self.obj.bvParams["Pb1"][0], 1.90916)
        self.assertAlmostEqual(self.obj.rCutoff, 6)

    def test_buffer_area(self):

        self.obj.define_buffer_area()
        
        # The simplified structure used has a 5.9 Å cubic cell, which means a 5x5x5 supercell is required for 6 Å cutoff radius
        self.assertArrayAlmostEqual(self.obj.bufferArea, np.array((5,5,5)))

        # For a 5x5x5 supercell, the core cell should be located at (3,3,3)
        self.assertArrayAlmostEqual(self.obj.findCoreCell(self.obj.bufferArea), np.array((2,2,2)))
        # self.assertArrayAlmostEqual(self.obj.coreCartesian, np.array((11.8612, 11.8612, 11.8612)))

        # Check the required volume parameters
        self.assertArrayAlmostEqual(self.obj.reqVolStart, np.array((-6, -6, -6)))
        self.assertArrayAlmostEqual(self.obj.reqVolEnd, np.array((11.9306, 11.9306, 11.9306)))

        # self.assertArrayAlmostEqual(self.obj.reqVolStart, np.array((5.8612, 5.8612, 5.8612)))
        # self.assertArrayAlmostEqual(self.obj.reqVolEnd, np.array((23.7918, 23.7918, 23.7918)))

    def test_find_buffered_sites(self):

        self.obj.define_buffer_area()
        self.obj.find_buffer_sites()

        # Estimate of upper limit of possible sites. If was simple 5x5x5 supercell, would be 500 sites
        self.assertLess(len(self.obj.bufferedSites), 172)
        self.assertGreaterEqual(len(self.obj.bufferedSites), 108)
        logging.info(f"test_find_buffered_sites - For simplifed PbF2, the are {len(self.obj.bufferedSites)} buffered sites")
        logging.info(f"test_find_buffered_sites - The following sites were created {self.obj.bufferedSites.to_string()}")

        for i, site in self.obj.bufferedSites.iterrows():
            self.assertTrue(self.obj.inside_space(self.obj.reqVolStart, self.obj.reqVolEnd, site["coords"]))

    def test_voxel_setup(self):

        self.obj.resolution = 0.5
        self.obj.setup_voxels()

        # Should generate 12x12x12 grid for 5.9 Å cubic cell at 0.5 Å resolution
        self.assertArrayAlmostEqual(self.obj.voxelNumbers, np.array((12,12,12)))
        self.assertTupleEqual(self.obj.map.shape, (12,12,12))


class TestBVStructureInternalMethods(alteredTestCase):

    def setUp(self):
        self.obj = calculate.BVStructure.from_file("test/betaPbF2-simplified.inp")

    def test_translate_custom_vectors(self):
        self.obj.vectors = np.array([[1,1,0],[0,1,0],[0,5,1]])
        coordinate = np.array((1,1.5,1))
        self.assertArrayAlmostEqual(self.obj.translate_coord(coordinate, (1,0,0)), np.array([2,2.5,1]))
        self.assertArrayAlmostEqual(self.obj.translate_coord(coordinate, (1,3,4)), np.array([2,25.5,5]))
        self.assertArrayAlmostEqual(self.obj.translate_coord(coordinate, (1,0.2,0)), np.array([2,2.7,1]))

    def test_translate_vectors(self):
        coordinate = np.array((4,5,7))
        # with self.assertRaises(Exception):
        #     self.struct.translateCoord(coordinate, (1.54,23.1,0))
        self.assertArrayAlmostEqual(self.obj.translate_coord(coordinate, (1,1,1)), np.array((9.9306,10.9306,12.9306)))

    def test_frac_from_cart(self):

        self.assertArrayAlmostEqual(self.obj._frac_from_cart(np.zeros(3)), np.zeros(3))
        self.assertArrayAlmostEqual(self.obj._frac_from_cart(np.sum(self.obj.vectors, axis=0)), np.array((1,1,1)))
        self.assertArrayAlmostEqual(self.obj._frac_from_cart(np.array((1.976866667, 1.976866667, 3.953733333))), np.array((0.3333333, 0.3333333, 0.666666667)))
        self.assertArrayAlmostEqual(self.obj._frac_from_cart(np.array((8.30284, 8.30284, 8.30284))), np.array((1.4, 1.4, 1.4)))
        self.assertArrayAlmostEqual(self.obj._frac_from_cart(np.array((-2.9653, -5.9306, 5.9306))), np.array((-0.5, -1, 1)))

    def test_cart_from_frac(self):

        rounder = np.vectorize(lambda x: round(x, 5))

        self.assertArrayAlmostEqual(self.obj._cart_from_frac(np.zeros(3)), np.zeros(3))
        self.assertArrayAlmostEqual(self.obj._cart_from_frac(np.array((1,1,1))), np.sum(self.obj.vectors, axis=0))
        self.assertArrayAlmostEqual(rounder(self.obj._cart_from_frac(np.array((0.3333333, 0.3333333, 0.666666667)))), rounder(np.array((1.976866667, 1.976866667, 3.953733333))))
        self.assertArrayAlmostEqual(self.obj._cart_from_frac(np.array((1.4, 1.4, 1.4))), np.array((8.30284, 8.30284, 8.30284)))
        self.assertArrayAlmostEqual(self.obj._cart_from_frac(np.array((-0.5, -1, 1))), np.array((-2.9653, -5.9306, 5.9306)))

    def test_insideSpace(self):
        start = np.array((0,0,0))
        end = np.array((1,1,1))

        # Normal Inbounds -> True
        self.assertTrue(self.obj.inside_space(start, end, np.array((0.1,0.5,0.7))))
        # On one edge -> True
        self.assertTrue(self.obj.inside_space(start, end, np.array((0,1,0.7))))
        # On one of the corners -> True
        self.assertTrue(self.obj.inside_space(start, end, np.array((1,1,1))))
        # Only one coordinate outside -> False
        self.assertFalse(self.obj.inside_space(start, end, np.array((-0.5,1,1))))
        # All coordinates outside -> False
        self.assertFalse(self.obj.inside_space(start, end, np.array((-0.5,2.5,99))))
        self.assertFalse(self.obj.inside_space(start, end, np.array((-0.5,0.5,-2))))

    def test_calc_voxel_cartesian(self):

        self.obj.voxelNumbers = np.array((10,10,10))
        self.obj.coreCartesian = np.array((5,2.5,5))
        self.obj.vectors = np.array(((5,0,0),(0.5,5,0),(0,0,10)))

        # Check no shift
        self.assertArrayAlmostEqual(self.obj.calc_voxel_cartesian(np.array((0,0,0))), np.array((0,0,0)))

        # Check very simple shift
        self.assertArrayAlmostEqual(self.obj.calc_voxel_cartesian(np.array((1,0,0))), np.array((0.5,0,0)))

        # Check all shifts at once
        self.assertArrayAlmostEqual(self.obj.calc_voxel_cartesian(np.array((1,1,1))), np.array((0.55,0.5,1)))

        # Check non-one values
        self.assertArrayAlmostEqual(self.obj.calc_voxel_cartesian(np.array((1,2,5))), np.array((0.6,1,5)))

        # Check decimals
        self.assertArrayAlmostEqual(self.obj.calc_voxel_cartesian(np.array((0.2,2,0))), np.array((0.2,1,0)))

    def test_distance_w_cutoff(self):

        self.obj.rCutoff = 6

        # Check very simple calculation
        self.assertAlmostEqual(self.obj.calc_distance(np.array((0,0,0)), np.array((1,1,1))), math.sqrt(3))
        
        # Check completely normal conditions
        self.assertAlmostEqual(self.obj.calc_distance(np.array((3,4,1)), np.array((-1,0,-2))), math.sqrt(41))
        
        # Check behaviour when very near cutoff
        self.assertAlmostEqual(self.obj.calc_distance(np.array((3,4,1)), np.array((-3,0,-2))), math.sqrt(61))

        # Check behaviour when beyond cutoff
        self.assertAlmostEqual(self.obj.calc_distance(np.array((3,4,1)), np.array((-10,0,-2))), 13)


class TestVectorBVS(alteredTestCase):

    def setUp(self):
        self.obj = calculate.BVStructure.from_file("test/pbsnf4-for-testing.inp")

    def test_distance_w_cutoff_vector(self):

        self.obj.rCutoff = 6

        # Check very simple calculation
        self.assertAlmostEqual(self.obj.calc_vector_distance(np.array((1,1,1)) - np.array((0,0,0))), math.sqrt(3))
        
        # Check completely normal conditions
        self.assertAlmostEqual(self.obj.calc_vector_distance(np.array((3,4,1)) - np.array((-1,0,-2))), math.sqrt(41))
        
        # Check behaviour when very near cutoff
        self.assertAlmostEqual(self.obj.calc_vector_distance(np.array((3,4,1)) - np.array((-3,0,-2))), math.sqrt(61))

        # Check behaviour when beyond cutoff
        self.assertAlmostEqual(self.obj.calc_vector_distance(np.array((3,4,1)) - np.array((-10,0,-2))), 13) 

    def test_find_site_vbvs(self):

        self.obj.initalise_map(1)

        snResult = self.obj.find_site_bvs("Sn1-0", vector=True)
        logging.info(f"Sn VBVS Result - {snResult}")
        self.assertAlmostEqual(snResult[0], 0)
        self.assertTrue(-1.15 < snResult[2] < -1.10)

    # def test_lone_pair_creation(self):

    #     self.obj.initaliseMap(1)
    #     self.obj.createLonePairs()
    #     logging.debug(self.obj.sites)
    #     self.obj.dfToCif("cif-files/lp5.cif")


    # def test_lone_pair_creation2(self):

    #     self.obj = bv2.BVStructure.from_file("files/ksbf4.inp")
    #     self.obj.initaliseMap(1)
    #     self.obj.createLonePairs()
    #     logging.debug(self.obj.sites)
    #     self.obj.dfToCif("cif-files/ksbf4lp.cif")

class TestLonePairs(alteredTestCase):

    def setUp(self):
        self.obj = calculate.BVStructure.from_file("test/pbsnf4-for-testing.inp")

    def test_linear_penalty_function(self):
        self.assertAlmostEqual(self.obj._linear_penalty(-2, 2, 0.5), 1/3)
        self.assertAlmostEqual(self.obj._linear_penalty(-2, 6, 0.5), 0)

    def test_quadratic_penalty_function(self):

        self.assertAlmostEqual(self.obj._quadratic_penalty(-2, 2, 0.5), 2/9)
        self.assertAlmostEqual(self.obj._quadratic_penalty(-2, 6, 0.5), 0)
    