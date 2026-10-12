public int[] dailyTemperatures(int[] temperatures) {
       int[] answer = new int[temperatures.length];
       Stack<Integer> stack = new Stack<>();
       
       for (int i = 0; i < temperatures.length; i++) {
           while (!stack.isEmpty() && temperatures[i] > temperatures[stack.peek()]) {
               int prevIndex = stack.pop();
               answer[prevIndex] = i - prevIndex;
           }
           stack.push(i);
       }
       
       return answer;
   }