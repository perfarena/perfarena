List<String> generateStringArray(int n) {
    List<String> answer = new ArrayList<>();
    
    for (int i = 1; i <= n; i++) {
        if (i % 3 == 0 && i % 5 == 0) {
            answer.add("fees Buzz");
        } else if (i % 3 == 0) {
            answer.add("fees");
        } else if (i % 5 == 0) {
            answer.add("Buzz");
        } else {
            answer.add(String.valueOf(i));
        }
    }
    
    return answer;
}