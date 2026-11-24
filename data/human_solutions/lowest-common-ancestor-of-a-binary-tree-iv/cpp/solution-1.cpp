for (int i = 0; i < s.length(); ++i) {
    if (s[i] == ' ' && --k == 0) {
        return s.substr(0, i);
    }
}
return s.substr(0, s.length());