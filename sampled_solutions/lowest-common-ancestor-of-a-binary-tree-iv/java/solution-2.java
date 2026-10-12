public String truncateSentence(String s, int k) {
    StringBuilder sb = new StringBuilder();
    int spaceCount = 0;

    for (char c : s.toCharArray()) {
        if (c == ' ') {
            spaceCount++;
            if (spaceCount == k) {
                break;
            }
        }
        sb.append(c);
    }

    return sb.toString().trim();
}