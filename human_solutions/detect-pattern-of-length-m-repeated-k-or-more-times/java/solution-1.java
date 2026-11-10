int answer = 0;
for (char c : n.toCharArray()) {
    answer = Math.max(answer, c - '0');
}
return answer;