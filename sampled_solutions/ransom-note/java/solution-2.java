public boolean canConstruct(String ransomNote, String magazine) {
    int[] map = new int[26];

    for (char c : magazine.toCharArray()) {
        map[c - 'a']++;
    }

    for (char c : ransomNote.toCharArray()) {
        int index = c - 'a';
        if (map[index] == 0) {
            return false;
        }
        map[index]--;
    }

    return true;
}