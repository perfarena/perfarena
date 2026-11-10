public boolean canMakeEqual(String[] words) {
    int[] charCounts = new int[26]; // Array to store character occurrences
    
    // Update character counts in the array
    for (String word : words) {
        for (char c : word.toCharArray()) {
            charCounts[c - 'a']++;
        }
    }
    
    // Check if character counts are divisible by the length of the array
    for (int count : charCounts) {
        if (count % words.length != 0) {
            return false;
        }
    }
    
    return true;
}