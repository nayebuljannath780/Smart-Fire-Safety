"""
Fire Environment Simulation

This file represents the physical environment
of the building.
"""

import random


class FireEnvironment:


    def __init__(self):

        self.temperature = 25
        self.smoke = 0
        self.humidity = 50



    def generate_data(self, fire=False):

        if fire:

            # Fire condition

            self.temperature = random.randint(70,100)

            self.smoke = random.randint(70,100)

            self.humidity = random.randint(30,50)



        else:

            # Normal condition

            self.temperature = random.randint(20,35)

            self.smoke = random.randint(0,20)

            self.humidity = random.randint(45,60)



        return {

            "temperature": self.temperature,

            "smoke": self.smoke,

            "humidity": self.humidity

        }