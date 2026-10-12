public int findMinDeciBinaryNumbers(String n) {
    int answer = 0;
    char[] digits = n.toCharArray();
    
    for (char digit : digits) {
        int num = Character.getNumericValue(digit);
        answer = Math.max(answer, num);
    }
    
    return answer;
}