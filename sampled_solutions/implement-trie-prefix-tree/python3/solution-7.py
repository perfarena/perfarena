class TrieNode:
    def __init__(self):
        self.children = {}  # Using a hashmap to store children nodes
        self.end_of_word = False  # Flag to mark the end of a word