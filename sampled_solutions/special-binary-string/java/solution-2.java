public List<Integer> partitionLabels(String s) {
    int[] lastPosition = new int[26]; // Array to store the last position of each character
    List<Integer> result = new ArrayList<>();
    int anchor = 0, j = 0;

    for (int i = 0; i < s.length(); i++) {
        lastPosition[s.charAt(i) - 'a'] = i;
    }

    for (int i = 0; i < s.length(); i++) {
        j = Math.max(j, lastPosition[s.charAt(i) - 'a']);
        if (i == j) {
            result.add(j - anchor + 1);
            anchor = i + 1;
        }
    }

    return result;
}