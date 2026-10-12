// Create a hash map for the magazine characters
unordered_map<char, int> mag;
for (char c : magazine) {
    if (mag.find(c) != mag.end()) {
        mag[c]++;
    } else {
        mag[c] = 1;
    }
}

// Iterate through the characters of the ransom note
for (char c : ransomNote) {
    if (mag.find(c) == mag.end() || mag[c] == 0) {
        return false;
    } else {
        mag[c]--;
    }
}

return true;