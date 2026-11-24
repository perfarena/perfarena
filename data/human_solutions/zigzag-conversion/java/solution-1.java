public String convert(String s, int numRows) {
    if (numRows == 1) {
        return s;
    }

    StringBuilder result = new StringBuilder();
    int n = s.length();

    for (int row = 0; row < numRows; row++) {
        int index = row;
        boolean first = true;

        while (index < n) {
            result.append(s.charAt(index));

            if (row != 0 && row != numRows - 1) {
                int nextIndex = first ? (numRows - row - 1) * 2 : row * 2;
                first = !first;
                index += nextIndex;
            } else {
                index += numRows * 2 - 2;
            }
        }
    }

    return result.toString();
}