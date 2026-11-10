// Create a map to store character frequencies
Map<Character, Integer> charMap = new HashMap<>();

// Iterate through each character in the strings
for (String word : words) {
    for (char c : word.toCharArray()) {
        charMap.put(c, charMap.getOrDefault(c, 0) + 1);
    }
}

// Calculate the total length of the strings
int totalLength = words.length;

// Check if the frequencies match the length
for (int freq : charMap.values()) {
    if (freq != totalLength) {
        return false;
    }
}

return true;