public String largestOddNumber(String num) {
    if ((num.charAt(num.length() - 1) - '0') % 2 == 1) {
        return num;
    } else {
        int i = num.length() - 2;
        while (i >= 0 && (num.charAt(i) - '0') % 2 == 0) {
            i--;
        }
        if (i < 0) {
            return "";
        } else {
            return num.substring(0, i + 1);
        }
    }
}