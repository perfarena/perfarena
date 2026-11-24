int findMaxDigit(string n) {
    int max_digit = 0;
    for (char c : n) {
        max_digit = std::max(max_digit, c - '0');
    }
    return max_digit;
}