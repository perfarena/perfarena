def remove(self, node):
        node.previous.next = node.next
        node.next.previous = node.previous