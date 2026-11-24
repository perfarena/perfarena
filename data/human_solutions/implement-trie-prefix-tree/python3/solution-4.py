class Trie:
    def __init__(self):
        self.root = TrieNode('')

    def insert(self, word):
        root = self.root
        for char in word:
            if char not in root.children:
                root.children[char] = TrieNode(char)
            root = root.children[char]
        root.is_last = True