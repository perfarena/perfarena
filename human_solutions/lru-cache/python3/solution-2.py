class LRUCache:
    def __init__(self, capacity):
        self.capacity = capacity
        self.cache = {}
        self.left = Node()  # Least Recently Used
        self.right = Node()  # Most Recently Used
        self.left.next = self.right
        self.right.previous = self.left