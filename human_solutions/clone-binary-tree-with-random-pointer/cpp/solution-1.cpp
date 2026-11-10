int maxLengthBetweenEqualCharacters(string s) {
    int maxSize = -1;
    unordered_map<char, int> charIndex;

    for (int i = 0; i < s.length(); i++) {
        char c = s[i];
        if (charIndex.find(c) == charIndex.end()) {
            charIndex[c] = i;
        } else {
            int currentSize = i - charIndex[c] - 1;
            maxSize = max(maxSize, currentSize);
        }
    }

    return maxSize;
}