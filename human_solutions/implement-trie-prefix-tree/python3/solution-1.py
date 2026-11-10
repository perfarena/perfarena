class Trie:
    def __init__(self):
        self.children = {}

    def insert(self, word):
        node = self.children
        for w in word:
            if w not in node:
                node[w] = {}
            node = node[w]
        node['end'] = True

    def search(self, word):
        node = self.children
        for w in word:
            if w not in node:
                return False
            node = node[w]
        return 'end' in node

    def startsWith(self, prefix):
        node = self.children
        for p in prefix:
            if p not in node:
                return False
            node = node[p]
        return True

# Example Usage
trie = Trie()
trie.insert("cat")
trie.insert("cats")
print(trie.search("cat"))  # Output: True
print(trie.startsWith("ca"))  # Output: True