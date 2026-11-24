for (int i = num.length() - 1; i >= 0; i--) {
    if ((num.charAt(i) - '0') % 2 != 0) {
        return num.substring(0, i + 1);
    }
}
return ""; // Return empty string if no odd integer is found