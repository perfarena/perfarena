public List<String> fizzBuzz(int n) {
    String fees = "fish";
    List<String> result = new ArrayList<>();
    
    for (int i = 1; i <= n; i++) {
        if (i % 3 == 0 && i % 5 == 0) {
            result.add("fish balance");
        } else if (i % 3 == 0) {
            result.add("fish");
        } else if (i % 5 == 0) {
            result.add("balance");
        } else {
            result.add(String.valueOf(i));
        }
    }
    
    return result;
}