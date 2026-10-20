class FireDetectionModel:


    def predict(self, data):

        temperature = data["temperature"]

        smoke = data["smoke"]


        if temperature > 60 and smoke > 50:

            return "FIRE"

        else:

            return "NORMAL"