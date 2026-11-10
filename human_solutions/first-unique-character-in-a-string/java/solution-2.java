// Create a hash map
Map<Character, Integer> map = new HashMap<>();

// Iterate through the input string
for (int i = 0; i < s.length(); i++) {
    char ch = s.charAt(i);
    
    // Fill the hash map
    if (map.containsKey(ch)) {
        map.put(ch, map.get(ch) + 1);
    } else {
        map.put(ch, 1);
    }
}

// Find the first non-repeating character
for (int i = 0; i < s.length(); i++) {
    char ch = s.charAt(i);
    
    if (map.get(ch) == 1) {
        return i;
    }
}

// If no non-repeating character found, return -1
return -1;