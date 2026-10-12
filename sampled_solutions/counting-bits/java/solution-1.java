// Helper function to count the number of ones in a binary string
int countOnes(String num) {
    int count = 0;
    for (int i = 0; i < num.length(); i++) {
        if (num.charAt(i) == '1') {
            count++;
        }
    }
    return count;
}

// Main function to solve the problem
int[] countBits(int n) {
    int[] answer = new int[n + 1];
    answer[0] = 0;

    for (int i = 1; i <= n; i++) {
        String binary = Integer.toBinaryString(i);
        int oneCount = countOnes(binary);
        answer[i] = oneCount;
    }

    return answer;
}