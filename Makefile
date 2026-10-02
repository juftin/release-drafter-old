.PHONY: all test lint sync clean

all: lint test

# Lint and validate all YAML configurations and regex patterns
lint:
	@echo "==> Validating YAML files..."
	@ruby -ryaml -e 'Dir.glob("**/*.yaml").each { |f| next if f.start_with?("vendor/"); YAML.load_file(f); puts "✅ Valid: #{f}" }'

# Run full configuration test suite
test: lint
	@echo "==> Verifying Gitmoji configuration sync..."
	@python3 scripts/generate_gitmoji.py
	@git diff --exit-code configs/ .github/release-drafter* || (echo "❌ Out of sync! Run 'make sync' and commit the changes." && exit 1)
	@echo "==> Running deep configuration and regex validation..."
	@ruby -ryaml -e '\
		configs = Dir.glob("configs/*.yaml") + Dir.glob(".github/release-drafter*.yaml"); \
		configs.each do |f| \
			data = YAML.load_file(f); \
			(data["autolabeler"] || []).each do |rule| \
				(Array(rule["title"]) + Array(rule["branch"])).each do |p| \
					if p.is_a?(String) && p.start_with?("/") && p.rindex("/") > 0; \
						l = p.rindex("/"); \
						re = p[1...l]; \
						flags = (p[(l+1)..-1] || "").include?("i") ? Regexp::IGNORECASE : 0; \
						Regexp.new(re, flags); \
					end; \
				end; \
			end; \
		end; \
		puts "✅ All configurations passed validation!"'

# Synchronize Gitmoji configs from data/gitmojis.json
sync:
	@python3 scripts/generate_gitmoji.py
	@echo "✅ Generated and synchronized all configuration files."
