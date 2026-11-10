def put(self, key, value):
        if key in self.cache:
            node = self.cache[key]
            self.remove(node)
        node = Node(key, value)
        self.cache[key] = node
        self.insert(node)
        
        if len(self.cache) > self.capacity:
            lru_node = self.left.next
            self.remove(lru_node)
            del self.cache[lru_node.key]