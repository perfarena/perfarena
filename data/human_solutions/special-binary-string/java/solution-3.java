public List<Integer> maxPartitionLengths(String s) {
    int[] endIdx = new int[26];
    List<Integer> result = new ArrayList<>();
    int start = 0, end = 0;

    for (int i = 0; i < s.length(); i++) {
        endIdx[s.charAt(i) - 'a'] = i;
    }

    for (int i = 0; i < s.length(); i++) {
        char c = s.charAt(i);
        int lastIdx = endIdx[c - 'a'];
        if (lastIdx > end) {
            end = lastIdx;
        }
        if (i == end) {
            result.add(end - start + 1);
            start = end + 1;
        }
    }

    return result;
}