public boolean makeEqual(String[] words) {
    if (words.length == 1) {
        return true;
    }

    int[] freq = new int[26];

    for (String word : words) {
        for (char c : word.toCharArray()) {
            freq[c - 'a']++;
        }
    }

    for (int num : freq) {
        if (num % words.length != 0) {
            return false;
        }
    }

    return true;
}