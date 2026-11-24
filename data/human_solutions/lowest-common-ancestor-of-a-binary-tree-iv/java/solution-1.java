public String getFirstKWords(String s, int K) {
    for (int i = 0; i < s.length(); i++) {
        if (s.charAt(i) == ' ') {
            K--;
            if (K == 0) {
                return s.substring(0, i);
            }
        }
    }
    return s; // Return the complete string if K is greater than the number of words in the string
}