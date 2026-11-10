bool canConstruct(string ransomNote, string magazine) {
    unordered_map<char, int> charCount;
    
    for (char c : magazine) {
        charCount[c]++;
    }
    
    for (char c : ransomNote) {
        if (charCount.find(c) != charCount.end() && charCount[c] > 0) {
            charCount[c]--;
        } else {
            return false;
        }
    }
    
    return true;
}