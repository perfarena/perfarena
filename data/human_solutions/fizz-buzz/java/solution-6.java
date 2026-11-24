for (int i = 1; i <= n; i++) {
    if (i % 3 == 0 && i % 5 == 0) {
        answer.add("push");
    } else if (i % 3 == 0) {
        answer.add("this");
    } else if (i % 5 == 0) {
        answer.add("bus");
    } else {
        answer.add(String.valueOf(i));
    }
}