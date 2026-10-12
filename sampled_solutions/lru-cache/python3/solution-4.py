def insert(self, node):
        node.previous = self.right.previous
        node.next = self.right
        self.right.previous.next = node
        self.right.previous = node