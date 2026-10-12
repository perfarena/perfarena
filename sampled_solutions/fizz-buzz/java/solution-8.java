public List<String> fizzBuzz(int n) {
    List<String> answer = new ArrayList<>();
    for (int num = 1; num <= n; num++) {
        if (num % 3 == 0 && num % 5 == 0) {
            answer.add("FizzBuzz");
        } else if (num % 3 == 0) {
            answer.add("Fizz");
        } else if (num % 5 == 0) {
            answer.add("Buzz");
        } else {
            answer.add(String.valueOf(num));
        }
    }
    return answer;
}