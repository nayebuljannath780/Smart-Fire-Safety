class SensorNode:


    def __init__(self, node_id):

        self.node_id = node_id



    def collect_data(self, data):

        data["node_id"] = self.node_id

        return data