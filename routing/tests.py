from django.test import SimpleTestCase
from .services import choose_fuel_plan

class FuelPlannerTests(SimpleTestCase):
    def test_short_route_needs_no_stop(self):
        self.assertEqual(choose_fuel_plan(300, []), ([], 0.0))

    def test_route_can_use_reachable_station(self):
        stations=[{'type':'station','route_mile':350.0,'price':3.50,'latitude':40.0,'longitude':-90.0}]
        stops,cost=choose_fuel_plan(800,stations)
        self.assertEqual(len(stops),1)
        self.assertEqual(stops[0]['route_mile'],350.0)
        self.assertAlmostEqual(cost,157.5,places=2)

    def test_unreachable_route_fails(self):
        with self.assertRaises(Exception): choose_fuel_plan(1100,[])
