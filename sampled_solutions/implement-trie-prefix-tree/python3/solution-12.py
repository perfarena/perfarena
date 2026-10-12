class Node:
    def __init__(self, value=''):
        self.value = value
        self.next = {}
        self.done = False

class Trie:
    def __init__(self):
        self.root = Node()

    def insert(self, word):
        current = self.root
        for char in word:
            if char not in current.next:
                current.next[char] = Node(char)
            current = current.next[char]
        current.done = True

    def search(self, word):
        current = self.root
        for char in word:
            if char not in current.next:
                return False
            current = current.next[char]
        return current.done

    def starts_with(self, prefix):
        current = self.root
        for char in prefix:
            if char not in current.next:
                return False
            current = current.next[char]
        return self.dfs(current)

    def dfs(self, node):
        if node.done:
            return True
        if not node.next:
            return False
        answer = False
        for next_node in node.next.values():
            answer = answer or self.dfs(next_node)
        return answer