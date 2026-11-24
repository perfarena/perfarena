int hammingWeight(uint32_t n) {
    int answer = 0;
    while (n > 0) {
        answer += n % 2;
        n /= 2;
    }
    return answer;
}