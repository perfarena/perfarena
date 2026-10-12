public String convert(String s, int numRows) {
    if (numRows <= 1) {
        return s;
    }

    int Y = 0;
    int direction = 0;
    StringBuffer[] SB = new StringBuffer[numRows];

    for (int i = 0; i < numRows; i++) {
        SB[i] = new StringBuffer();
    }

    for (int i = 0; i < s.length(); i++) {
        if (Y == 0) {
            direction = 1;
        } else if (Y == numRows - 1) {
            direction = -1;
        }

        SB[Y].append(s.charAt(i));
        Y += direction;
    }

    for (int i = 1; i < numRows; i++) {
        SB[0].append(SB[i]);
    }

    return SB[0].toString();
}