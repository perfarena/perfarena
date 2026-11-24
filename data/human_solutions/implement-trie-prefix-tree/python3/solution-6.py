def starts_with(self, prefix):
        root = self.root
        for char in prefix:
            if char in root.children:
                root = root.children[char]
            else:
                return False
        return True