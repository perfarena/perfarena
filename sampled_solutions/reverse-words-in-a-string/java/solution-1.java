String reverseWords(String s) {
    String result = "";
    int n = s.length();
    int i = 0;

    while (i < n) {
        if (result.isEmpty()) {
            result = s.substring(i, i + 1);
        } else {
            result = s.substring(i, i + 1) + " " + result;
        }

        int j = i + 1;
        while (j < n && s.charAt(j) != ' ') {
            j++;
        }

        String word = s.substring(i, j);
        if (!result.isEmpty()) {
            result = word + " " + result;
        } else {
            result = word;
        }

        i = j + 1;
    }

    return result;
}