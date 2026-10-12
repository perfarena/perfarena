def search(self, word):
        root = self.root
        for char in word:
            if char in root.children:
                root = root.children[char]
            else:
                return False
        return root.is_last