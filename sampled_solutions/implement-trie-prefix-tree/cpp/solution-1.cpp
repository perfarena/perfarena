class Trie {
private:
    Trie* children[26];
    bool isEndOfWord;
public:
    Trie() {
        isEndOfWord = false;
        for (int i = 0; i < 26; ++i) {
            children[i] = nullptr;
        }
    }
    
    void insert(string word) {
        Trie* current = this;
        for (char c : word) {
            int index = c - 'a';
            if (!current->children[index]) {
                current->children[index] = new Trie();
            }
            current = current->children[index];
        }
        current->isEndOfWord = true;
    }
    
    bool search(string word) {
        Trie* current = this;
        for (char c : word) {
            int index = c - 'a';
            if (!current->children[index]) {
                return false;
            }
            current = current->children[index];
        }
        return current && current->isEndOfWord;
    }
    
    bool startsWith(string prefix) {
        Trie* current = this;
        for (char c : prefix) {
            int index = c - 'a';
            if (!current->children[index]) {
                return false;
            }
            current = current->children[index];
        }
        return true;
    }
};