"""Code generation tools for the Development Agent.

The agent LLM generates code content in its reasoning; these tools
materialise that content into the correct on-disk structure.
"""
from __future__ import annotations

import json
import os
import pathlib
from pathlib import Path

from langchain_core.tools import tool

from agents_orchestrator.development_agent.config.session_state import get_session
from config.ws_helper import get_session_id, get_user_id

_FILES_DIR = str(pathlib.Path(__file__).resolve().parents[3] / "files")


def _get_work_dir() -> str:
    session_id = get_session_id()
    s = get_session(session_id)
    if s.work_dir:
        return s.work_dir
    user_id = get_user_id()
    work_dir = os.path.join(_FILES_DIR, str(user_id), "orchestrator", str(session_id), "project")
    os.makedirs(work_dir, exist_ok=True)
    return work_dir


def _resolve_path(file_path: str) -> Path:
    """Resolve file_path to an absolute Path inside the session workspace.

    If file_path is relative, it is joined with the session work_dir.
    If file_path is absolute but NOT inside the work_dir, it is re-rooted to work_dir.
    """
    p = Path(file_path)
    if not p.is_absolute():
        return Path(_get_work_dir()) / p
    work_dir = Path(_get_work_dir())
    try:
        p.relative_to(work_dir)
        return p  # already inside workspace
    except ValueError:
        # LLM gave a wrong absolute path — use only the relative portion
        return work_dir / p.name


# ── Scaffold templates ─────────────────────────────────────────────────────────

_SCAFFOLDS: dict[str, dict[str, str]] = {
    # ── Python ────────────────────────────────────────────────────────────────
    "fastapi": {
        "app/__init__.py": "",
        "app/main.py": 'from fastapi import FastAPI\n\napp = FastAPI()\n\n\n@app.get("/")\ndef root():\n    return {"status": "ok"}\n',
        "app/models/__init__.py": "",
        "app/routes/__init__.py": "",
        "app/services/__init__.py": "",
        "tests/__init__.py": "",
        "tests/test_main.py": "from fastapi.testclient import TestClient\nfrom app.main import app\n\nclient = TestClient(app)\n\n\ndef test_root():\n    response = client.get('/')\n    assert response.status_code == 200\n",
        "requirements.txt": "fastapi\nuvicorn[standard]\npydantic\nhttpx\n",
        ".gitignore": "__pycache__/\n*.pyc\n.env\n.venv/\n",
    },
    "django": {
        "manage.py": "#!/usr/bin/env python\nimport sys\n\ndef main():\n    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')\n    from django.core.management import execute_from_command_line\n    execute_from_command_line(sys.argv)\n\nif __name__ == '__main__':\n    main()\n",
        "config/__init__.py": "",
        "config/settings.py": "from pathlib import Path\nBASE_DIR = Path(__file__).resolve().parent.parent\nSECRET_KEY = 'change-me'\nDEBUG = True\nINSTALLED_APPS = ['django.contrib.admin','django.contrib.auth','django.contrib.contenttypes']\nDATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3','NAME': BASE_DIR / 'db.sqlite3'}}\n",
        "config/urls.py": "from django.urls import path\nurlpatterns = []\n",
        "apps/__init__.py": "",
        "requirements.txt": "django\ndjangorestframework\n",
        ".gitignore": "__pycache__/\n*.pyc\n.env\ndb.sqlite3\n",
    },
    "flask": {
        "app/__init__.py": "from flask import Flask\n\ndef create_app():\n    app = Flask(__name__)\n    from .routes import main\n    app.register_blueprint(main)\n    return app\n",
        "app/routes.py": "from flask import Blueprint, jsonify\n\nmain = Blueprint('main', __name__)\n\n@main.route('/')\ndef index():\n    return jsonify({'status': 'ok'})\n",
        "app/models.py": "",
        "app/services.py": "",
        "tests/__init__.py": "",
        "tests/test_app.py": "import pytest\nfrom app import create_app\n\n@pytest.fixture\ndef client():\n    app = create_app()\n    app.config['TESTING'] = True\n    with app.test_client() as c:\n        yield c\n\ndef test_index(client):\n    r = client.get('/')\n    assert r.status_code == 200\n",
        "run.py": "from app import create_app\napp = create_app()\nif __name__ == '__main__':\n    app.run(debug=True)\n",
        "requirements.txt": "flask\nflask-sqlalchemy\nflask-migrate\npytest\n",
        ".gitignore": "__pycache__/\n*.pyc\n.env\n.venv/\n",
    },
    # ── JavaScript / TypeScript ────────────────────────────────────────────────
    "react": {
        "src/index.jsx": "import React from 'react';\nimport ReactDOM from 'react-dom/client';\nimport App from './App';\nReactDOM.createRoot(document.getElementById('root')).render(<App />);\n",
        "src/App.jsx": "import React from 'react';\nexport default function App() { return <div>Hello</div>; }\n",
        "src/components/.gitkeep": "",
        "src/hooks/.gitkeep": "",
        "src/utils/.gitkeep": "",
        "public/index.html": "<!DOCTYPE html><html><head><title>App</title></head><body><div id='root'></div></body></html>\n",
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"start":"react-scripts start","build":"react-scripts build","test":"react-scripts test"},"dependencies":{"react":"^18","react-dom":"^18","react-scripts":"5"}}\n',
        ".gitignore": "node_modules/\nbuild/\n.env\n",
    },
    "nextjs": {
        "pages/index.js": "export default function Home() { return <main><h1>Hello</h1></main>; }\n",
        "pages/_app.js": "export default function App({ Component, pageProps }) { return <Component {...pageProps} />; }\n",
        "components/.gitkeep": "",
        "lib/.gitkeep": "",
        "public/.gitkeep": "",
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"dev":"next dev","build":"next build","start":"next start"},"dependencies":{"next":"14","react":"^18","react-dom":"^18"}}\n',
        ".gitignore": "node_modules/\n.next/\nbuild/\n.env\n",
    },
    "express": {
        "src/index.js": "const express = require('express');\nconst app = express();\napp.use(express.json());\n\napp.get('/', (req, res) => res.json({ status: 'ok' }));\n\nconst PORT = process.env.PORT || 3000;\napp.listen(PORT, () => console.log(`Server running on port ${PORT}`));\n",
        "src/routes/.gitkeep": "",
        "src/controllers/.gitkeep": "",
        "src/models/.gitkeep": "",
        "src/middleware/.gitkeep": "",
        "tests/.gitkeep": "",
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"start":"node src/index.js","dev":"nodemon src/index.js","test":"jest"},"dependencies":{"express":"^4"},"devDependencies":{"nodemon":"^3","jest":"^29"}}\n',
        ".gitignore": "node_modules/\n.env\ndist/\n",
    },
    "nestjs": {
        "src/main.ts": "import { NestFactory } from '@nestjs/core';\nimport { AppModule } from './app.module';\n\nasync function bootstrap() {\n  const app = await NestFactory.create(AppModule);\n  await app.listen(3000);\n}\nbootstrap();\n",
        "src/app.module.ts": "import { Module } from '@nestjs/common';\nimport { AppController } from './app.controller';\nimport { AppService } from './app.service';\n\n@Module({\n  controllers: [AppController],\n  providers: [AppService],\n})\nexport class AppModule {}\n",
        "src/app.controller.ts": "import { Controller, Get } from '@nestjs/common';\nimport { AppService } from './app.service';\n\n@Controller()\nexport class AppController {\n  constructor(private readonly appService: AppService) {}\n\n  @Get()\n  getStatus() { return { status: 'ok' }; }\n}\n",
        "src/app.service.ts": "import { Injectable } from '@nestjs/common';\n\n@Injectable()\nexport class AppService {}\n",
        "test/.gitkeep": "",
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"start":"nest start","dev":"nest start --watch","build":"nest build","test":"jest"},"dependencies":{"@nestjs/common":"^10","@nestjs/core":"^10","@nestjs/platform-express":"^10","reflect-metadata":"^0.1","rxjs":"^7"},"devDependencies":{"@nestjs/cli":"^10","@nestjs/testing":"^10","typescript":"^5","ts-jest":"^29","jest":"^29"}}\n',
        "tsconfig.json": '{"compilerOptions":{"module":"commonjs","declaration":true,"removeComments":true,"emitDecoratorMetadata":true,"experimentalDecorators":true,"target":"ES2021","outDir":"./dist","baseUrl":"./"}}\n',
        ".gitignore": "node_modules/\ndist/\n.env\n",
    },
    "angular": {
        "src/main.ts": "import { bootstrapApplication } from '@angular/platform-browser';\nimport { AppComponent } from './app/app.component';\nbootstrapApplication(AppComponent);\n",
        "src/app/app.component.ts": "import { Component } from '@angular/core';\n\n@Component({\n  selector: 'app-root',\n  template: '<h1>Hello Angular</h1>',\n  standalone: true,\n})\nexport class AppComponent {}\n",
        "src/app/app.module.ts": "",
        "src/app/services/.gitkeep": "",
        "src/app/components/.gitkeep": "",
        "src/app/models/.gitkeep": "",
        "src/index.html": "<!DOCTYPE html><html><head><meta charset='utf-8'><title>App</title></head><body><app-root></app-root></body></html>\n",
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"start":"ng serve","build":"ng build","test":"ng test"},"dependencies":{"@angular/common":"^17","@angular/core":"^17","@angular/platform-browser":"^17","rxjs":"^7","zone.js":"~0.14"},"devDependencies":{"@angular/cli":"^17","@angular/compiler-cli":"^17","typescript":"~5.2"}}\n',
        "tsconfig.json": '{"compilerOptions":{"outDir":"./dist","strict":true,"noImplicitOverride":true,"noPropertyAccessFromIndexSignature":true,"noImplicitReturns":true,"noFallthroughCasesInSwitch":true,"esModuleInterop":true,"experimentalDecorators":true,"moduleResolution":"bundler","importHelpers":true,"target":"ES2022","module":"ES2022","lib":["ES2022","dom"]}}\n',
        ".gitignore": "node_modules/\ndist/\n.env\n",
    },
    "vue": {
        "src/main.js": "import { createApp } from 'vue';\nimport App from './App.vue';\ncreateApp(App).mount('#app');\n",
        "src/App.vue": "<template>\n  <div id='app'>\n    <h1>Hello Vue</h1>\n    <router-view />\n  </div>\n</template>\n\n<script setup>\n</script>\n",
        "src/components/.gitkeep": "",
        "src/views/.gitkeep": "",
        "src/store/.gitkeep": "",
        "src/router/index.js": "import { createRouter, createWebHistory } from 'vue-router';\nconst routes = [];\nexport default createRouter({ history: createWebHistory(), routes });\n",
        "public/index.html": "<!DOCTYPE html><html><head><title>App</title></head><body><div id='app'></div></body></html>\n",
        "package.json": '{"name":"app","version":"1.0.0","scripts":{"dev":"vite","build":"vite build","preview":"vite preview"},"dependencies":{"vue":"^3","vue-router":"^4","pinia":"^2"},"devDependencies":{"@vitejs/plugin-vue":"^4","vite":"^5"}}\n',
        ".gitignore": "node_modules/\ndist/\n.env\n",
    },
    # ── .NET / C# ──────────────────────────────────────────────────────────────
    "dotnet": {
        "src/Program.cs": 'var builder = WebApplication.CreateBuilder(args);\nbuilder.Services.AddControllers();\nbuilder.Services.AddEndpointsApiExplorer();\nbuilder.Services.AddSwaggerGen();\n\nvar app = builder.Build();\nif (app.Environment.IsDevelopment()) { app.UseSwagger(); app.UseSwaggerUI(); }\napp.UseHttpsRedirection();\napp.UseAuthorization();\napp.MapControllers();\napp.Run();\n',
        "src/Controllers/StatusController.cs": 'using Microsoft.AspNetCore.Mvc;\n\nnamespace App.Controllers;\n\n[ApiController]\n[Route("api/[controller]")]\npublic class StatusController : ControllerBase\n{\n    [HttpGet]\n    public IActionResult Get() => Ok(new { status = "ok" });\n}\n',
        "src/Models/.gitkeep": "",
        "src/Services/.gitkeep": "",
        "src/Repositories/.gitkeep": "",
        "tests/.gitkeep": "",
        "App.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web">\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n  </PropertyGroup>\n</Project>\n',
        ".gitignore": "bin/\nobj/\n*.user\n.vs/\n.env\n",
    },
    "aspnet": {
        "src/Program.cs": 'var builder = WebApplication.CreateBuilder(args);\nbuilder.Services.AddControllersWithViews();\nbuilder.Services.AddDbContext<AppDbContext>();\n\nvar app = builder.Build();\napp.UseStaticFiles();\napp.UseRouting();\napp.MapControllerRoute(name: "default", pattern: "{controller=Home}/{action=Index}/{id?}");\napp.Run();\n',
        "src/Controllers/HomeController.cs": 'using Microsoft.AspNetCore.Mvc;\n\nnamespace App.Controllers;\n\npublic class HomeController : Controller\n{\n    public IActionResult Index() => View();\n}\n',
        "src/Models/.gitkeep": "",
        "src/Views/Home/Index.cshtml": "@{\n    ViewData[\"Title\"] = \"Home\";\n}\n<h1>Hello ASP.NET</h1>\n",
        "src/Data/AppDbContext.cs": "using Microsoft.EntityFrameworkCore;\n\nnamespace App.Data;\n\npublic class AppDbContext : DbContext\n{\n    public AppDbContext(DbContextOptions<AppDbContext> options) : base(options) {}\n}\n",
        "App.csproj": '<Project Sdk="Microsoft.NET.Sdk.Web">\n  <PropertyGroup>\n    <TargetFramework>net8.0</TargetFramework>\n    <Nullable>enable</Nullable>\n    <ImplicitUsings>enable</ImplicitUsings>\n  </PropertyGroup>\n  <ItemGroup>\n    <PackageReference Include="Microsoft.EntityFrameworkCore.SqlServer" Version="8.*" />\n    <PackageReference Include="Microsoft.EntityFrameworkCore.Tools" Version="8.*" />\n  </ItemGroup>\n</Project>\n',
        ".gitignore": "bin/\nobj/\n*.user\n.vs/\n.env\n",
    },
    # ── Java ───────────────────────────────────────────────────────────────────
    "spring-boot": {
        "src/main/java/com/app/Application.java": "package com.app;\n\nimport org.springframework.boot.SpringApplication;\nimport org.springframework.boot.autoconfigure.SpringBootApplication;\n\n@SpringBootApplication\npublic class Application {\n    public static void main(String[] args) {\n        SpringApplication.run(Application.class, args);\n    }\n}\n",
        "src/main/java/com/app/controller/StatusController.java": "package com.app.controller;\n\nimport org.springframework.web.bind.annotation.*;\nimport java.util.Map;\n\n@RestController\n@RequestMapping(\"/api\")\npublic class StatusController {\n    @GetMapping(\"/status\")\n    public Map<String, String> status() {\n        return Map.of(\"status\", \"ok\");\n    }\n}\n",
        "src/main/java/com/app/model/.gitkeep": "",
        "src/main/java/com/app/service/.gitkeep": "",
        "src/main/java/com/app/repository/.gitkeep": "",
        "src/main/resources/application.properties": "spring.application.name=app\nserver.port=8080\n",
        "src/test/java/com/app/ApplicationTests.java": "package com.app;\n\nimport org.junit.jupiter.api.Test;\nimport org.springframework.boot.test.context.SpringBootTest;\n\n@SpringBootTest\nclass ApplicationTests {\n    @Test\n    void contextLoads() {}\n}\n",
        "pom.xml": '<?xml version="1.0" encoding="UTF-8"?>\n<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"\n  xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">\n  <modelVersion>4.0.0</modelVersion>\n  <parent>\n    <groupId>org.springframework.boot</groupId>\n    <artifactId>spring-boot-starter-parent</artifactId>\n    <version>3.2.0</version>\n  </parent>\n  <groupId>com.app</groupId>\n  <artifactId>app</artifactId>\n  <version>0.0.1-SNAPSHOT</version>\n  <dependencies>\n    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-web</artifactId></dependency>\n    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-data-jpa</artifactId></dependency>\n    <dependency><groupId>org.springframework.boot</groupId><artifactId>spring-boot-starter-test</artifactId><scope>test</scope></dependency>\n  </dependencies>\n</project>\n',
        ".gitignore": "target/\n*.class\n.env\n",
    },
    # ── Go ─────────────────────────────────────────────────────────────────────
    "go": {
        "main.go": 'package main\n\nimport (\n\t"encoding/json"\n\t"net/http"\n)\n\nfunc main() {\n\thttp.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {\n\t\tw.Header().Set("Content-Type", "application/json")\n\t\tjson.NewEncoder(w).Encode(map[string]string{"status": "ok"})\n\t})\n\thttp.ListenAndServe(":8080", nil)\n}\n',
        "internal/handlers/.gitkeep": "",
        "internal/models/.gitkeep": "",
        "internal/services/.gitkeep": "",
        "internal/repository/.gitkeep": "",
        "go.mod": "module app\n\ngo 1.21\n",
        ".gitignore": "*.exe\n*.exe~\n*.dll\n*.so\n*.dylib\nbuild/\n.env\n",
    },
    # ── PHP ────────────────────────────────────────────────────────────────────
    "laravel": {
        "app/Http/Controllers/StatusController.php": "<?php\n\nnamespace App\\Http\\Controllers;\n\nuse Illuminate\\Http\\JsonResponse;\n\nclass StatusController extends Controller\n{\n    public function index(): JsonResponse\n    {\n        return response()->json(['status' => 'ok']);\n    }\n}\n",
        "app/Models/.gitkeep": "",
        "app/Services/.gitkeep": "",
        "routes/api.php": "<?php\n\nuse Illuminate\\Support\\Facades\\Route;\nuse App\\Http\\Controllers\\StatusController;\n\nRoute::get('/status', [StatusController::class, 'index']);\n",
        "tests/Feature/.gitkeep": "",
        "tests/Unit/.gitkeep": "",
        "composer.json": '{"name":"app/app","require":{"php":"^8.2","laravel/framework":"^11.0"},"require-dev":{"phpunit/phpunit":"^11.0"},"autoload":{"psr-4":{"App\\\\":"app/"}}}\n',
        ".gitignore": "vendor/\n.env\nstorage/\nbootstrap/cache/\n",
    },
    # ── Ruby ───────────────────────────────────────────────────────────────────
    "rails": {
        "app/controllers/application_controller.rb": "class ApplicationController < ActionController::API\nend\n",
        "app/controllers/status_controller.rb": "class StatusController < ApplicationController\n  def index\n    render json: { status: 'ok' }\n  end\nend\n",
        "app/models/.gitkeep": "",
        "app/services/.gitkeep": "",
        "config/routes.rb": "Rails.application.routes.draw do\n  get '/status', to: 'status#index'\nend\n",
        "db/migrate/.gitkeep": "",
        "spec/.gitkeep": "",
        "Gemfile": "source 'https://rubygems.org'\nruby '3.3.0'\ngem 'rails', '~> 7.1'\ngem 'pg'\ngem 'puma'\ngroup :test do\n  gem 'rspec-rails'\nend\n",
        ".gitignore": ".bundle/\nlog/\ntmp/\n*.log\n.env\n",
    },
}

# Canonical aliases so the agent can pass common variations
_ALIASES: dict[str, str] = {
    "asp.net": "aspnet",
    "asp": "aspnet",
    ".net": "dotnet",
    "dotnet-api": "dotnet",
    "dotnet-webapi": "dotnet",
    "springboot": "spring-boot",
    "spring_boot": "spring-boot",
    "java-spring": "spring-boot",
    "java": "spring-boot",
    "node": "express",
    "nodejs": "express",
    "node.js": "express",
    "nest": "nestjs",
    "nest.js": "nestjs",
    "next": "nextjs",
    "next.js": "nextjs",
    "vue.js": "vue",
    "vuejs": "vue",
    "react.js": "react",
    "reactjs": "react",
    "golang": "go",
    "php": "laravel",
    "ruby": "rails",
    "ruby-on-rails": "rails",
    "ror": "rails",
    "python": "fastapi",
    "python-api": "fastapi",
}


def _write_scaffold(root: Path, files: dict) -> list[str]:
    created = []
    for rel_path, content in files.items():
        full = root / rel_path
        full.parent.mkdir(parents=True, exist_ok=True)
        if not full.exists():
            full.write_text(content, encoding="utf-8")
        created.append(rel_path)
    return created


def _tree(root: Path, prefix: str = "", depth: int = 0) -> str:
    if depth > 4:
        return ""
    lines = []
    try:
        entries = sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name))
    except PermissionError:
        return ""
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry.name}")
        if entry.is_dir():
            extension = "    " if i == len(entries) - 1 else "│   "
            lines.append(_tree(entry, prefix + extension, depth + 1))
    return "\n".join(filter(None, lines))


# ── Tools ──────────────────────────────────────────────────────────────────────

def _dynamic_scaffold(stack: str) -> dict[str, str]:
    """Minimal universal scaffold for any stack not in _SCAFFOLDS."""
    return {
        "src/.gitkeep": "",
        "src/main/.gitkeep": "",
        "tests/.gitkeep": "",
        ".gitignore": f"# {stack}\nbuild/\ndist/\n.env\n*.log\n",
        "README.md": (
            f"# {stack.title()} Project\n\n"
            "Scaffold created by the Development Agent.\n"
            "Use `generate_component` / `write_file` to add source files.\n"
        ),
    }


@tool
async def generate_project_scaffold(project_name: str, tech_stack: str, output_dir: str = "") -> str:
    """Create a standard project folder structure for the given tech stack.

    Supports any technology — well-known stacks get full boilerplate templates,
    unknown stacks get a minimal universal scaffold so generation never fails.

    Built-in stacks with full templates:
      Python:  fastapi, django, flask
      JS/TS:   react, nextjs, express, nestjs, angular, vue
      .NET/C#: dotnet, aspnet
      Java:    spring-boot
      Go:      go
      PHP:     laravel
      Ruby:    rails

    Common aliases accepted: java, .net, asp.net, node, nodejs, springboot,
    golang, ruby-on-rails, vue.js, react.js, etc.

    Comma-separate for multi-tier projects, e.g. "spring-boot,react".

    Args:
        project_name: Name of the project (used as the root folder name).
        tech_stack: Technology stack(s), comma-separated.
        output_dir: Parent directory for the scaffold. Leave empty to use the session workspace.

    Returns a directory tree of what was created.
    """
    stacks = [s.strip().lower() for s in tech_stack.split(",")]
    session = get_session(get_session_id())
    work_dir = _get_work_dir()

    if session.work_dir:
        # Cloned repo already exists — scaffold directly into the repo root, not a subfolder.
        # A subfolder would misalign work_dir and break all subsequent generate_component paths.
        root = Path(session.work_dir)
    else:
        resolved_output = output_dir.strip() if output_dir and output_dir.strip() else work_dir
        root = Path(resolved_output) / project_name
        session.work_dir = str(root)

    root.mkdir(parents=True, exist_ok=True)

    created_files: list[str] = []
    used_templates: list[str] = []

    for stack in stacks:
        canonical = _ALIASES.get(stack, stack)
        template = _SCAFFOLDS.get(canonical)

        prefix = canonical if len(stacks) > 1 else ""
        sub_root = root / prefix if prefix else root

        if template:
            created_files += _write_scaffold(sub_root, template)
            used_templates.append(f"{stack} → built-in template ({canonical})")
        else:
            created_files += _write_scaffold(sub_root, _dynamic_scaffold(stack))
            used_templates.append(
                f"{stack} → minimal scaffold (no built-in template; "
                "use generate_component/write_file to add source files)"
            )

    tree = _tree(root)
    template_note = "\n".join(f"  • {t}" for t in used_templates)
    return (
        f"Project scaffold created at: {root}\n\n"
        f"Templates used:\n{template_note}\n\n"
        f"{project_name}/\n{tree}\n\n"
        f"Created {len(created_files)} files. Ready for component generation.\n"
        f"Use relative paths (e.g. 'src/App.jsx') in generate_component — the workspace root is set."
    )


@tool
async def generate_component(file_path: str, code_content: str, description: str = "") -> str:
    """Write a generated code component to disk inside the session workspace.

    Always use RELATIVE paths (e.g. 'src/components/Button.jsx', 'utils/math.py').
    The workspace root is set automatically after clone_repo or generate_project_scaffold.

    Args:
        file_path: Relative path inside the project (e.g. 'src/App.jsx').
        code_content: The complete source code (required, never empty).
        description: Short description for the activity log.
    """
    if not code_content or not code_content.strip():
        return (
            "ERROR: code_content is required but was empty or missing. "
            "Call generate_component again with code_content=<the complete code>. "
            "Never call this tool without providing the full source code."
        )
    path = _resolve_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code_content, encoding="utf-8")
    lines = code_content.count("\n") + 1
    label = f" ({description})" if description else ""
    # Track in session artifacts so files_updated broadcast fires
    s = get_session(get_session_id())
    rel = str(path.relative_to(Path(_get_work_dir()))).replace("\\", "/")
    if rel not in s.dev_artifacts.generated_files:
        s.dev_artifacts.generated_files.append(rel)
    return f"Component written{label}: {rel} ({lines} lines)"


@tool
async def generate_api_endpoint(file_path: str, code_content: str, endpoint_summary: str = "") -> str:
    """Write a generated API endpoint file to disk.

    IMPORTANT: You MUST provide the complete source code as 'code_content'.
    Generate REST endpoint code in your reasoning (including request/response
    models, validation, error handling), then call this tool to materialise it.

    Args:
        file_path: Absolute path for the endpoint file.
        code_content: Complete source code including route handler and models (required).
        endpoint_summary: Short description, e.g. "POST /users — create user".

    Returns confirmation with file path and route summary.
    """
    if not code_content or not code_content.strip():
        return (
            "Error: code_content is empty. Generate the full endpoint source code, "
            "then call generate_api_endpoint with both file_path and code_content."
        )
    path = _resolve_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code_content, encoding="utf-8")
    lines = code_content.count("\n") + 1
    label = f" [{endpoint_summary}]" if endpoint_summary else ""
    s = get_session(get_session_id())
    rel = str(path.relative_to(Path(_get_work_dir()))).replace("\\", "/")
    if rel not in s.dev_artifacts.generated_files:
        s.dev_artifacts.generated_files.append(rel)
    return f"API endpoint written{label}: {rel} ({lines} lines)"


@tool
async def generate_database_migration(
    file_path: str, migration_content: str, orm_type: str = "sqlalchemy"
) -> str:
    """Write a database migration file to disk.

    IMPORTANT: You MUST provide the complete migration as 'migration_content'.
    Generate the migration in your reasoning (ORM model changes or raw SQL),
    then call this tool to write it.

    Args:
        file_path: Absolute path for the migration file.
        migration_content: Full migration source (Alembic, Django, raw SQL, etc.) (required).
        orm_type: One of: sqlalchemy, django, alembic, raw_sql.

    Returns confirmation with file path.
    """
    if not migration_content or not migration_content.strip():
        return (
            "Error: migration_content is empty. Generate the full migration source, "
            "then call generate_database_migration with both file_path and migration_content."
        )
    path = _resolve_path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(migration_content, encoding="utf-8")
    lines = migration_content.count("\n") + 1
    s = get_session(get_session_id())
    rel = str(path.relative_to(Path(_get_work_dir()))).replace("\\", "/")
    if rel not in s.dev_artifacts.generated_files:
        s.dev_artifacts.generated_files.append(rel)
    return f"Migration written ({orm_type}): {rel} ({lines} lines)"
