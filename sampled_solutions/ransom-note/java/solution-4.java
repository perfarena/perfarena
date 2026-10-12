// Create a map for the magazine characters
Map<Character, Integer> mag = new HashMap<>();
for (char c : magazine.toCharArray()) {
    mag.put(c, mag.getOrDefault(c, 0) + 1);
}

// Iterate through the characters of the ransom note
for (char c : ransomNote.toCharArray()) {
    if (!mag.containsKey(c) || mag.get(c) == 0) {
        return false;
    } else {
        mag.put(c, mag.get(c) - 1);
    }
}

return true;