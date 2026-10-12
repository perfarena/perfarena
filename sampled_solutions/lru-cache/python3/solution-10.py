# Define the private member variables in the class
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.map = OrderedDict()

    # Implement the get method
    def get(self, key: int) -> int:
        return self.map.get(key, -1)

    # Implement the put method
    def put(self, key: int, value: int) -> None:
        if key in self.map:
            del self.map[key]
        elif len(self.map) >= self.capacity:
            self.map.popitem(last=False)
        self.map[key] = value