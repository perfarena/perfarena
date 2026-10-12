public int maxLengthBetweenEqualCharacters(String s) {
    int[] firstOccurrence = new int[26];
    Arrays.fill(firstOccurrence, -1);
    int answer = -1;

    for (int i = 0; i < s.length(); i++) {
        char ch = s.charAt(i);
        int index = ch - 'a';
        
        if (firstOccurrence[index] == -1) {
            firstOccurrence[index] = i;
        } else {
            answer = Math.max(answer, i - firstOccurrence[index] - 1);
        }
    }

    return answer;
}