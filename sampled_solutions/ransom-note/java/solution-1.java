int[] map = new int[26];

// Iterate over the magazine string
for (char c : magazine.toCharArray()) {
    map[c - 'a']++;
}

// Iterate over the ransom note string
for (char c : ransomNote.toCharArray()) {
    int index = c - 'a';
    if (map[index] == 0) {
        return false;
    }
    map[index]--;
}

return true;