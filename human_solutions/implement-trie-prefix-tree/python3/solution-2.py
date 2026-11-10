class Trie:
    def __init__(self):
        self.children = [None] * 26
        self.isEndOfWord = False
    
    def insert(self, word):
        current = self
        for c in word:
            index = ord(c) - ord('a')
            if not current.children[index]:
                current.children[index] = Trie()
            current = current.children[index]
        current.isEndOfWord = True
    
    def search(self, word):
        current = self
        for c in word:
            index = ord(c) - ord('a')
            if not current.children[index]:
                return False
            current = current.children[index]
        return current is not None and current.isEndOfWord
    
    def startsWith(self, prefix):
        current = self
        for c in prefix:
            index = ord(c) - ord('a')
            if not current.children[index]:
                return False
            current = current.children[index]
        return True