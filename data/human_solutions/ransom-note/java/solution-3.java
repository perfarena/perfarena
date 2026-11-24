// Method signature for canConstruct
public boolean canConstruct(String ransomNote, String magazine) {
    // Basic validation
    if (ransomNote == null || magazine == null) {
        return false;
    }

    int[] charCount = new int[26];

    // Count characters in the magazine
    for (char c : magazine.toCharArray()) {
        charCount[c - 'a']++;
    }

    // Decrement character counts for ransom note
    for (char c : ransomNote.toCharArray()) {
        if (--charCount[c - 'a'] < 0) {
            return false; // Character shortage
        }
    }

    return true; // Sufficient characters available
}

// Main method to test the canConstruct method
public static void main(String[] args) {
    // Test cases
    System.out.println(canConstruct("a", "b")); // false
    System.out.println(canConstruct("aa", "ab")); // false
    System.out.println(canConstruct("aa", "aab")); // true
}