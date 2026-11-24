class TrieNode:
    def __init__(self, char):
        self.char = char
        self.children = {}
        self.is_last = False