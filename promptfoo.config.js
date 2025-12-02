module.exports = {
  default: {
    provider: 'openai',
    model: 'gpt-4o-mini'
  },
  tests: [
    {
      name: 'analyzer-returns-json',
      prompt: 'Analyze: ERROR: sample failure',
      assert: [
        { path: '$.diagnosis', exists: true },
        { path: '$.fixes', type: 'array' }
      ]
    }
  ]
}
