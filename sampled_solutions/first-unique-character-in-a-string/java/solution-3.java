public int firstUniqChar(String s) {
    Map<Character, Integer> count = new HashMap<>();
    
    // Populate the hash map with character counts
    for (char c : s.toCharArray()) {
        count.put(c, count.getOrDefault(c, 0) + 1);
    }
    
    // Find the first non-repeating character
    for (int i = 0; i < s.length(); i++) {
        if (count.get(s.charAt(i)) == 1) {
            return i;
        }
    }
    
    return -1;
}