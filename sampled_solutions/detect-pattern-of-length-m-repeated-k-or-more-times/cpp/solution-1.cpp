int answer = 0;
for (char c : n) {
    answer = std::max(answer, c - '0');
}
return answer;